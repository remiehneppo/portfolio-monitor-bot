import requests
import json
import argparse
import csv
import os

# ERC721 Transfer event signature
# Transfer(address indexed from, address indexed to, uint256 indexed tokenId)
ERC721_TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)

# ERC1155 event signatures
TRANSFER_SINGLE_TOPIC = (
    "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"
)
TRANSFER_BATCH_TOPIC = (
    "0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb"
)

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
ZERO_TOPIC = "0x" + "0" * 64


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


def fetch_mint_events_erc721(endpoint, headers, blockchain, contract_address, page_size):
    """
    Fetch ERC721 mint events (Transfer from 0x0).
    Returns list of dicts: {block_number, txn, wallet, id}
    """
    mints = []
    page_token = ""
    page = 1

    while True:
        payload = {
            "id": 1,
            "jsonrpc": "2.0",
            "method": "ankr_getLogs",
            "params": {
                "blockchain": [blockchain],
                "address": [contract_address],
                "topics": [
                    [ERC721_TRANSFER_TOPIC],
                    [ZERO_TOPIC],  # from = 0x0 (mint)
                ],
                "pageSize": page_size,
                "pageToken": page_token,
            },
        }

        result = ankr_request(endpoint, headers, payload)
        logs = result.get("logs", [])

        for log in logs:
            topics = log.get("topics", [])
            if len(topics) < 4:
                continue

            from_addr = "0x" + topics[1][-40:].lower()
            if from_addr != ZERO_ADDRESS:
                continue

            to_addr = "0x" + topics[2][-40:].lower()
            token_id = int(topics[3], 16)
            block_number = log.get("blockNumber")
            tx_hash = log.get("transactionHash", "")

            # blockNumber may come as hex string
            if isinstance(block_number, str):
                block_number = int(block_number, 16) if block_number.startswith("0x") else int(block_number)

            mints.append({
                "block_number": block_number,
                "txn": tx_hash,
                "wallet": to_addr,
                "id": token_id,
            })

        print(f"  Page {page}: {len(logs)} Transfer events, {len(mints)} mints so far")

        next_token = result.get("nextPageToken")
        if not next_token or not logs:
            break
        page_token = next_token
        page += 1

    return mints


def fetch_mint_events_erc1155_single(endpoint, headers, blockchain, contract_address, page_size):
    """
    Fetch ERC1155 TransferSingle mint events (from = 0x0).
    Returns list of dicts: {block_number, txn, wallet, id}
    """
    mints = []
    page_token = ""
    page = 1

    while True:
        payload = {
            "id": 1,
            "jsonrpc": "2.0",
            "method": "ankr_getLogs",
            "params": {
                "blockchain": [blockchain],
                "address": [contract_address],
                "topics": [
                    [TRANSFER_SINGLE_TOPIC],
                    [],            # operator (any)
                    [ZERO_TOPIC],  # from = 0x0 (mint)
                ],
                "pageSize": page_size,
                "pageToken": page_token,
            },
        }

        result = ankr_request(endpoint, headers, payload)
        logs = result.get("logs", [])

        for log in logs:
            topics = log.get("topics", [])
            if len(topics) < 4:
                continue

            from_addr = "0x" + topics[2][-40:].lower()
            if from_addr != ZERO_ADDRESS:
                continue

            to_addr = "0x" + topics[3][-40:].lower()
            data_hex = log.get("data", "0x")
            raw = data_hex[2:]
            token_id = int(raw[0:64], 16)
            # value = int(raw[64:128], 16)  # quantity minted

            block_number = log.get("blockNumber")
            tx_hash = log.get("transactionHash", "")

            if isinstance(block_number, str):
                block_number = int(block_number, 16) if block_number.startswith("0x") else int(block_number)

            mints.append({
                "block_number": block_number,
                "txn": tx_hash,
                "wallet": to_addr,
                "id": token_id,
            })

        print(f"  Page {page}: {len(logs)} TransferSingle events, {len(mints)} mints so far")

        next_token = result.get("nextPageToken")
        if not next_token or not logs:
            break
        page_token = next_token
        page += 1

    return mints


def fetch_mint_events_erc1155_batch(endpoint, headers, blockchain, contract_address, page_size):
    """
    Fetch ERC1155 TransferBatch mint events (from = 0x0).
    Each batch event can contain multiple token IDs — one row per token ID.
    Returns list of dicts: {block_number, txn, wallet, id}
    """
    mints = []
    page_token = ""
    page = 1

    while True:
        payload = {
            "id": 1,
            "jsonrpc": "2.0",
            "method": "ankr_getLogs",
            "params": {
                "blockchain": [blockchain],
                "address": [contract_address],
                "topics": [
                    [TRANSFER_BATCH_TOPIC],
                    [],            # operator (any)
                    [ZERO_TOPIC],  # from = 0x0 (mint)
                ],
                "pageSize": page_size,
                "pageToken": page_token,
            },
        }

        result = ankr_request(endpoint, headers, payload)
        logs = result.get("logs", [])

        for log in logs:
            topics = log.get("topics", [])
            if len(topics) < 4:
                continue

            from_addr = "0x" + topics[2][-40:].lower()
            if from_addr != ZERO_ADDRESS:
                continue

            to_addr = "0x" + topics[3][-40:].lower()
            data_hex = log.get("data", "0x")
            raw = data_hex[2:]

            # ABI decode: offset_ids | offset_values | ids_len | ids... | vals_len | vals...
            offset_ids = int(raw[0:64], 16) * 2
            ids_len = int(raw[offset_ids : offset_ids + 64], 16)
            ids = [
                int(raw[offset_ids + 64 + i * 64 : offset_ids + 128 + i * 64], 16)
                for i in range(ids_len)
            ]

            block_number = log.get("blockNumber")
            tx_hash = log.get("transactionHash", "")

            if isinstance(block_number, str):
                block_number = int(block_number, 16) if block_number.startswith("0x") else int(block_number)

            for token_id in ids:
                mints.append({
                    "block_number": block_number,
                    "txn": tx_hash,
                    "wallet": to_addr,
                    "id": token_id,
                })

        print(f"  Page {page}: {len(logs)} TransferBatch events, {len(mints)} mints so far")

        next_token = result.get("nextPageToken")
        if not next_token or not logs:
            break
        page_token = next_token
        page += 1

    return mints


def get_nft_mints(blockchain, contract_address, api_key, nft_type="auto", page_size=1000):
    """
    Get all mint transactions for an NFT contract.
    nft_type: "erc721", "erc1155", or "auto" (try ERC721 first, then ERC1155).
    Returns sorted list of mint dicts.
    """
    endpoint = f"https://rpc.ankr.com/multichain/{api_key}"
    headers = {"Content-Type": "application/json"}

    mints = []

    if nft_type in ("auto", "erc721"):
        print("[ERC721] Fetching Transfer mint events...")
        erc721_mints = fetch_mint_events_erc721(
            endpoint, headers, blockchain, contract_address, page_size
        )
        mints.extend(erc721_mints)
        print(f"  => {len(erc721_mints)} ERC721 mints found")

        if nft_type == "auto" and erc721_mints:
            # Found ERC721 mints, skip ERC1155
            print("  Detected as ERC721, skipping ERC1155 scan.")
            mints.sort(key=lambda x: (x["block_number"], x["id"]))
            return mints

    if nft_type in ("auto", "erc1155"):
        print("[ERC1155] Fetching TransferSingle mint events...")
        single_mints = fetch_mint_events_erc1155_single(
            endpoint, headers, blockchain, contract_address, page_size
        )
        mints.extend(single_mints)
        print(f"  => {len(single_mints)} ERC1155 TransferSingle mints found")

        print("[ERC1155] Fetching TransferBatch mint events...")
        batch_mints = fetch_mint_events_erc1155_batch(
            endpoint, headers, blockchain, contract_address, page_size
        )
        mints.extend(batch_mints)
        print(f"  => {len(batch_mints)} ERC1155 TransferBatch mints found")

    mints.sort(key=lambda x: (x["block_number"], x["id"]))
    return mints


def export_mints_to_csv(mints, output_file):
    """Export mint list to CSV."""
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["block_number", "txn", "wallet", "id"])
        for m in mints:
            writer.writerow([m["block_number"], m["txn"], m["wallet"], m["id"]])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Get NFT mint transactions (ERC721 & ERC1155) by contract address using Ankr API and export to CSV"
    )
    parser.add_argument(
        "--blockchain",
        required=True,
        help="Blockchain name (e.g., eth, bsc, base, arbitrum, polygon, optimism)",
    )
    parser.add_argument("--contract", required=True, help="NFT contract address")
    parser.add_argument("--api_key", required=True, help="Your Ankr API token")
    parser.add_argument(
        "--type",
        choices=["auto", "erc721", "erc1155"],
        default="auto",
        help="NFT standard type (default: auto-detect)",
    )
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
        help="Comma-separated list of token IDs to filter (e.g. 1,2,3). Default: all IDs.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="Output CSV filename (default: nft_mints_<contract>_<blockchain>.csv)",
    )

    args = parser.parse_args()

    if not args.output:
        short_addr = args.contract[:10]
        args.output = f"nft_mints_{short_addr}_{args.blockchain}.csv"

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

        mints = get_nft_mints(
            args.blockchain, args.contract, args.api_key, args.type, args.page_size
        )

        if filter_ids:
            mints = [m for m in mints if m["id"] in filter_ids]
            print(f"After ID filter: {len(mints)} mint transactions")

        print()
        print(f"Total mint transactions: {len(mints)}")
        unique_wallets = set(m["wallet"] for m in mints)
        print(f"Unique minting wallets:  {len(unique_wallets)}")
        unique_ids = set(m["id"] for m in mints)
        print(f"Unique token IDs minted: {len(unique_ids)}")

        export_mints_to_csv(mints, args.output)
        print(f"Exported to: {os.path.abspath(args.output)}")

    except Exception as e:
        print(f"Error: {str(e)}")
