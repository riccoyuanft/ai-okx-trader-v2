import asyncio
import okx.Account as Account
from src.db.supabase_client import get_user_repo
from src.auth.crypto import decrypt

async def test_set_leverage():
    user_repo = get_user_repo()
    user = await user_repo.get_by_id("21c8f716-d609-4059-9472-58827ffb87aa")
    
    api_key = decrypt(user["okx_api_key"])
    secret = decrypt(user["okx_secret_key"])
    passphrase = decrypt(user["okx_passphrase"])
    
    account = Account.AccountAPI(api_key, secret, passphrase, False, "1")
    
    symbol = "BTC-USDT-SWAP"
    
    # Test 1: Set position mode to net (single direction)
    print("=== Test 1: Set position mode ===")
    try:
        result = account.set_position_mode(posMode="net_mode")
        print(f"Result: {result}")
    except Exception as e:
        print(f"Error: {e}")
    
    # Test 2: Set leverage without posSide
    print("\n=== Test 2: Set leverage without posSide ===")
    try:
        result = account.set_leverage(
            instId=symbol,
            lever="20",
            mgnMode="isolated"
        )
        print(f"Result: {result}")
    except Exception as e:
        print(f"Error: {e}")
    
    # Test 3: Set leverage with posSide="net"
    print("\n=== Test 3: Set leverage with posSide='net' ===")
    try:
        result = account.set_leverage(
            instId=symbol,
            lever="20",
            mgnMode="isolated",
            posSide="net"
        )
        print(f"Result: {result}")
    except Exception as e:
        print(f"Error: {e}")
    
    # Test 4: Set leverage with posSide="long"
    print("\n=== Test 4: Set leverage with posSide='long' ===")
    try:
        result = account.set_leverage(
            instId=symbol,
            lever="20",
            mgnMode="isolated",
            posSide="long"
        )
        print(f"Result: {result}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_set_leverage())
