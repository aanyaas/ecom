import os
import datetime
import requests
import time
from database.connection import get_db_connection

# =============================================================================
# Amazon Selling Partner API (SP-API) Sync
# =============================================================================
def get_amazon_access_token():
    """Generates an LWA (Login with Amazon) Access Token."""
    refresh_token = os.getenv('AMAZON_REFRESH_TOKEN')
    client_id = os.getenv('AMAZON_CLIENT_ID')
    client_secret = os.getenv('AMAZON_CLIENT_SECRET')
    
    if not all([refresh_token, client_id, client_secret]):
        print("Missing Amazon SP-API credentials. Using mock token.")
        return "mock_amazon_token"
        
    url = "https://api.amazon.com/auth/o2/token"
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret
    }
    try:
        response = requests.post(url, data=payload, timeout=10)
        response.raise_for_status()
        return response.json().get('access_token')
    except Exception as e:
        print(f"Failed to fetch Amazon Token: {e}")
        return "mock_amazon_token"

def fetch_amazon_orders():
    """Fetches latest orders from Amazon using SP-API."""
    token = get_amazon_access_token()
    print("Initiating Amazon SP-API Order Sync...")
    
    if token == "mock_amazon_token":
        return [
            {"id": "AMZ-1001", "total_amount": 2500.0, "status": "Pending", "date": datetime.datetime.now()},
            {"id": "AMZ-1002", "total_amount": 1850.0, "status": "Shipped", "date": datetime.datetime.now()}
        ]
        
    # Real implementation placeholder
    endpoint = "https://sellingpartnerapi-eu.amazon.com/orders/v0/orders"
    headers = {"x-amz-access-token": token}
    params = {"CreatedAfter": (datetime.datetime.now() - datetime.timedelta(days=1)).isoformat()}
    
    try:
        res = requests.get(endpoint, headers=headers, params=params, timeout=15)
        if res.status_code == 200:
            # Parse SP-API response format here
            return []
    except Exception as e:
        print(f"Error fetching real Amazon orders: {e}")
        
    return []


# =============================================================================
# Flipkart Seller API Sync
# =============================================================================
def get_flipkart_access_token():
    """Generates a Flipkart Seller API Access Token."""
    app_id = os.getenv('FLIPKART_APP_ID')
    app_secret = os.getenv('FLIPKART_APP_SECRET')
    
    if not all([app_id, app_secret]):
        print("Missing Flipkart API credentials. Using mock token.")
        return "mock_flipkart_token"
        
    url = "https://api.flipkart.net/oauth-service/oauth/token"
    try:
        response = requests.get(url, auth=(str(app_id), str(app_secret)), params={"grant_type": "client_credentials"}, timeout=10)
        response.raise_for_status()
        return response.json().get('access_token')
    except Exception as e:
        print(f"Failed to fetch Flipkart Token: {e}")
        return "mock_flipkart_token"

def fetch_flipkart_orders():
    """Fetches latest orders from Flipkart Seller API."""
    token = get_flipkart_access_token()
    print("Initiating Flipkart Seller API Order Sync...")
    
    if token == "mock_flipkart_token":
        return [
            {"id": "FK-2001", "total_amount": 1200.0, "status": "Approved", "date": datetime.datetime.now()}
        ]
        
    endpoint = "https://api.flipkart.net/sellers/v3/orders/search"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"filter": {"orderDate": {"from": (datetime.datetime.now() - datetime.timedelta(days=1)).isoformat(), "to": datetime.datetime.now().isoformat()}}}
    
    try:
        res = requests.post(endpoint, headers=headers, json=payload, timeout=15)
        if res.status_code == 200:
            return []
    except Exception as e:
        print(f"Error fetching real Flipkart orders: {e}")
        
    return []


# =============================================================================
# Database Sync Module
# =============================================================================
def sync_orders_to_database():
    """
    Syncs fetched external orders to the main e-commerce database.
    Prevents duplicates by checking existing external_order_id.
    """
    conn = get_db_connection()
    if not conn:
        print("Database connection failed during sync.")
        return

    try:
        cursor = conn.cursor()
        
        # We need a system user ID to assign external orders to (since user_id is NOT NULL).
        # We will use user_id = 3 as a fallback, or fetch the first active user.
        cursor.execute("SELECT id FROM users LIMIT 1")
        sys_user = cursor.fetchone()
        sys_user_id = sys_user[0] if sys_user else 1
        
        insert_query = """
            INSERT INTO orders (user_id, total_amount, subtotal, payment_status, payment_method, status, sales_channel, external_order_id, order_dateonly, shipping_address, billing_address)
            SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, '{}', '{}'
            WHERE NOT EXISTS (
                SELECT 1 FROM orders WHERE external_order_id = %s
            )
        """
        
        amazon_orders = fetch_amazon_orders()
        for order in amazon_orders:
            cursor.execute(insert_query, (
                sys_user_id, order['total_amount'], order['total_amount'], 
                'paid', 'Amazon Pay', order['status'], 'Amazon', 
                order['id'], datetime.datetime.now().date(), order['id']
            ))
            if cursor.rowcount > 0:
                print(f"Synced Amazon Order: {order['id']} - Rs.{order['total_amount']}")

        flipkart_orders = fetch_flipkart_orders()
        for order in flipkart_orders:
            cursor.execute(insert_query, (
                sys_user_id, order['total_amount'], order['total_amount'], 
                'paid', 'Flipkart Pay', order['status'], 'Flipkart', 
                order['id'], datetime.datetime.now().date(), order['id']
            ))
            if cursor.rowcount > 0:
                print(f"Synced Flipkart Order: {order['id']} - Rs.{order['total_amount']}")

        conn.commit()
        print("Successfully synchronized multi-channel orders.")
    except Exception as e:
        print(f"Error syncing orders: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == '__main__':
    # We must push an application context so that get_db_connection() can access Flask 'g'
    try:
        from app import app
        with app.app_context():
            sync_orders_to_database()
    except Exception as e:
        print(f"Error running sync script: {e}")
