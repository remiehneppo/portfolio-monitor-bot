import requests
import json
import argparse
import csv
import os
from collections import defaultdict

# ERC1155 event signatures
TRANSFER_SINGLE_TOPIC = (
    "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"
)
TRANSFER_BATCH_TOPIC = (
    "0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb"
)
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def ankr_request(endpoint, headers, payload):
    """Send a JSON-RPC request to Ankr API and return the result."""
    response = requests.post(endpoint, headers=headers, data=json.dumps(payload))
    if response.status_code != 200:
        raise Exception(
            f"API request failed with status {response.status_code}: {response.text}"
        )
    data = response.json()
    if "error" in data and data["error"]:
        raise Exception(f"API error: {data['error']}")
    return data.get("result", {})


# ---------------------------------------------------------------------------
# ERC721: ankr_getNFTHolders
# ---------------------------------------------------------------------------
def get_nft_holders_erc721(blockchain, contract_address, api_key, page_size=1000):
    """
    Query NFT holders using ankr_getNFTHolders (works for ERC721).
    Returns a list of holder wallet address strings.
    """
    endpoint = f"https://rpc.ankr.com/multichain/{api_key}"
    headers = {"Content-Type": "application/json"}

    payload = {
        "id": 1,
        "jsonrpc": "2.0",
        "method": "ankr_getNFTHolders",
        "params": {
            "blockchain": blockchain,
            "contractAddress": contract_address,
            "pageSize": page_size,
            "pageToken": "",
        },
    }

    all_holders = []
    page = 1
    while True:
        print(f"  Fetching page {page}...")
        result = ankr_request(endpoint, headers, payload)
        holders = result.get("holders", [])
        all_holders.extend(holders)
        print(f"  Got {len(holders)} holders (total so far: {len(all_holders)})")

        next_page_token = result.get("nextPageToken")
        if not next_page_token:
            break

        payload["params"]["pageToken"] = next_page_token
        page += 1

    return all_holders


# ---------------------------------------------------------------------------
# ERC1155: reconstruct holders from TransferSingle / TransferBatch logs
# ---------------------------------------------------------------------------
def parse_transfer_single(from_addr, to_addr, data_hex, balances):
    """Parse a TransferSingle event and update balances."""
    raw = data_hex[2:]  # strip 0x
    token_id = int(raw[0:64], 16)
    value = int(raw[64:128], 16)

    if from_addr != ZERO_ADDRESS:
        balances[from_addr][token_id] -= value
    if to_addr != ZERO_ADDRESS:
        balances[to_addr][token_id] += value


def parse_transfer_batch(from_addr, to_addr, data_hex, balances):
    """Parse a TransferBatch event and update balances."""
    raw = data_hex[2:]  # strip 0x
    # ABI: offset_ids (32B) | offset_values (32B) | ...arrays...
    offset_ids = int(raw[0:64], 16) * 2  # byte→hex-char offset
    offset_vals = int(raw[64:128], 16) * 2

    ids_len = int(raw[offset_ids : offset_ids + 64], 16)
    ids = [
        int(raw[offset_ids + 64 + i * 64 : offset_ids + 128 + i * 64], 16)
        for i in range(ids_len)
    ]

    vals_len = int(raw[offset_vals : offset_vals + 64], 16)
    vals = [
        int(raw[offset_vals + 64 + i * 64 : offset_vals + 128 + i * 64], 16)
        for i in range(vals_len)
    ]

    for token_id, value in zip(ids, vals):
        if from_addr != ZERO_ADDRESS:
            balances[from_addr][token_id] -= value
        if to_addr != ZERO_ADDRESS:
            balances[to_addr][token_id] += value


def _fetch_events(endpoint, headers, blockchain, contract_address, topic, balances,
                  page_size, is_batch):
    """Fetch all logs for a given event topic and update balances."""
    page_token = ""
    page = 1
    total_events = 0
    event_name = "TransferBatch" if is_batch else "TransferSingle"

    while True:
        payload = {
            "id": 1,
            "jsonrpc": "2.0",
            "method": "ankr_getLogs",
            "params": {
                "blockchain": [blockchain],
                "address": [contract_address],
                "topics": [[topic]],
                "pageSize": page_size,
                "pageToken": page_token,
            },
        }

        result = ankr_request(endpoint, headers, payload)
        logs = result.get("logs", [])
        total_events += len(logs)

        for log in logs:
            topics = log.get("topics", [])
            if len(topics) < 4:
                continue
            from_addr = "0x" + topics[2][-40:].lower()
            to_addr = "0x" + topics[3][-40:].lower()
            data = log.get("data", "0x")

            if is_batch:
                parse_transfer_batch(from_addr, to_addr, data, balances)
            else:
                parse_transfer_single(from_addr, to_addr, data, balances)

        print(f"    Page {page}: {len(logs)} {event_name} events (total: {total_events})")

        next_token = result.get("nextPageToken")
        if not next_token or not logs:
            break
        page_token = next_token
        page += 1

    return total_events


def get_nft_holders_erc1155(blockchain, contract_address, api_key, page_size=1000):
    """
    Build the holder list for an ERC1155 contract by parsing
    TransferSingle and TransferBatch events via ankr_getLogs.

    Returns:
        holders_by_token: dict  {token_id: {address: balance, ...}, ...}
        unique_holders:   set   all unique holder addresses across all token IDs
    """
    endpoint = f"https://rpc.ankr.com/multichain/{api_key}"
    headers = {"Content-Type": "application/json"}

    # balances[address][tokenId] = int
    balances = defaultdict(lambda: defaultdict(int))

    print("  Fetching TransferSingle events...")
    n_single = _fetch_events(
        endpoint, headers, blockchain, contract_address,
        TRANSFER_SINGLE_TOPIC, balances, page_size, is_batch=False,
    )

    print("  Fetching TransferBatch events...")
    n_batch = _fetch_events(
        endpoint, headers, blockchain, contract_address,
        TRANSFER_BATCH_TOPIC, balances, page_size, is_batch=True,
    )

    print(f"  Total events processed: {n_single + n_batch}")

    # Restructure: {token_id: {address: balance}} — only positive balances
    holders_by_token = defaultdict(dict)
    unique_holders = set()

    for addr, token_bals in balances.items():
        if addr == ZERO_ADDRESS:
            continue
        for token_id, bal in token_bals.items():
            if bal > 0:
                holders_by_token[token_id][addr] = bal
                unique_holders.add(addr)

    return holders_by_token, unique_holders


# ---------------------------------------------------------------------------
# Main logic: try ERC721 first, fallback to ERC1155
# ---------------------------------------------------------------------------
def get_nft_holders(blockchain, contract_address, api_key, page_size=1000):
    """
    Get NFT holders for any contract (ERC721 or ERC1155).
    Tries ankr_getNFTHolders first; if empty, falls back to ERC1155 log parsing.

    Returns:
        For ERC721:  (list_of_addresses, "ERC721", None)
        For ERC1155: (unique_holders_set, "ERC1155", holders_by_token_dict)
    """
    print("[1/2] Trying ankr_getNFTHolders (ERC721)...")
    holders = get_nft_holders_erc721(blockchain, contract_address, api_key, page_size)

    if holders:
        print(f"  => Found {len(holders)} holders via ankr_getNFTHolders")
        return holders, "ERC721", None

    print("  => No holders found. Falling back to ERC1155 transfer log analysis...")
    print()
    print("[2/2] Parsing ERC1155 transfer events via ankr_getLogs...")
    holders_by_token, unique_holders = get_nft_holders_erc1155(
        blockchain, contract_address, api_key, page_size
    )
    return unique_holders, "ERC1155", holders_by_token


def export_to_csv_erc721(holders, output_file, blockchain, contract_address):
    """Export ERC721 holder list to CSV."""
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["#", "holder_address", "blockchain", "contract_address"])
        for idx, holder in enumerate(holders, start=1):
            writer.writerow([idx, holder, blockchain, contract_address])


def export_to_csv_erc1155(holders_by_token, output_file, blockchain, contract_address):
    """
    Export ERC1155 holder list to CSV, grouped by token ID.
    Columns: token_id, holders_count, #, holder_address, balance
    """
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["token_id", "holders_count", "#", "holder_address", "balance",
             "blockchain", "contract_address"]
        )
        for token_id in sorted(holders_by_token.keys()):
            addr_bals = holders_by_token[token_id]
            holders_count = len(addr_bals)
            # Sort by balance descending, then address ascending
            sorted_holders = sorted(
                addr_bals.items(), key=lambda x: (-x[1], x[0])
            )
            for idx, (addr, bal) in enumerate(sorted_holders, start=1):
                writer.writerow(
                    [token_id, holders_count, idx, addr, bal,
                     blockchain, contract_address]
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Query NFT holders (ERC721 & ERC1155) by contract address using Ankr API and export to CSV"
    )
    parser.add_argument(
        "--blockchain",
        required=True,
        help="Blockchain name (e.g., eth, bsc, base, arbitrum, polygon, optimism)",
    )
    parser.add_argument("--contract", required=True, help="NFT contract address")
    parser.add_argument("--api_key", required=True, help="Your Ankr API token")
    parser.add_argument(
        "--page_size",
        type=int,
        default=1000,
        help="Number of results per page (max 10000, default 1000)",
    )
    parser.add_argument(
        "--ids",
        type=str,
        default="",
        help="Comma-separated list of token IDs to filter (e.g. 1,2,3). Only applied to ERC1155. Default: all IDs.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="Output CSV filename (default: nft_holders_<contract>_<blockchain>.csv)",
    )

    args = parser.parse_args()

    # Generate default output filename if not provided
    if not args.output:
        short_addr = args.contract[:10]
        args.output = f"nft_holders_{short_addr}_{args.blockchain}.csv"

    # Parse optional ID filter
    filter_ids = None
    if args.ids:
        filter_ids = set(int(i.strip()) for i in args.ids.split(",") if i.strip())

    try:
        print(f"Blockchain: {args.blockchain}")
        print(f"Contract:   {args.contract}")
        if filter_ids:
            print(f"Filter IDs: {sorted(filter_ids)}")
        print()

        holders, contract_type, holders_by_token = get_nft_holders(
            args.blockchain, args.contract, args.api_key, args.page_size
        )

        if filter_ids:
            if contract_type == "ERC1155" and holders_by_token:
                holders_by_token = {tid: v for tid, v in holders_by_token.items() if tid in filter_ids}
                holders = set(addr for tid_holders in holders_by_token.values() for addr in tid_holders)
                print(f"After ID filter: {len(holders_by_token)} token ID(s), {len(holders)} unique holders")
            else:
                print("  [Warning] --ids filter is not supported for ERC721 (ankr_getNFTHolders does not return per-token data).")

        print()
        print(f"Contract type: {contract_type}")
        print(f"Total unique holders: {len(holders)}")

        if contract_type == "ERC1155" and holders_by_token:
            print(f"Token IDs found: {len(holders_by_token)}")
            print()
            print("--- Holders per Token ID ---")
            for token_id in sorted(holders_by_token.keys()):
                count = len(holders_by_token[token_id])
                print(f"  Token ID {token_id}: {count} holders")
            print()
            export_to_csv_erc1155(
                holders_by_token, args.output, args.blockchain, args.contract
            )
        else:
            export_to_csv_erc721(
                holders, args.output, args.blockchain, args.contract
            )

        print(f"Exported to: {os.path.abspath(args.output)}")

    except Exception as e:
        print(f"Error: {str(e)}")
        