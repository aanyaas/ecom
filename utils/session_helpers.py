import uuid
from flask import session, request

def get_or_create_guest_session():
    """Manage guest visitor cookies and unique session IDs."""
    session.permanent = True
    if 'user_id' in session:
        return None
    if 'guest_id' not in session:
        cookie_guest_id = request.cookies.get('guest_id')
        if cookie_guest_id:
            session['guest_id'] = cookie_guest_id
        else:
            session['guest_id'] = str(uuid.uuid4())
        session.modified = True
    return session['guest_id']

from extensions import db
from models import Cart, GuestCart

def get_guest_or_user_cart_count():
    """Retrieve total item quantities in the user's active cart."""
    from flask import has_request_context
    if not has_request_context():
        return 0
    try:
        uid = session.get('user_id')
        gid = (session.get('guest_id') or request.cookies.get('guest_id')) if not uid else None
        
        if uid:
            items = db.session.scalars(db.select(Cart).filter_by(user_id=uid)).all()
            return sum(item.quantity for item in items)
        elif gid:
            items = db.session.scalars(db.select(GuestCart).filter_by(guest_id=gid)).all()
            return sum(item.quantity for item in items)
        else:
            return 0
    except Exception as e:
        print(f"Error getting cart count via ORM: {e}")
        return 0

from utils.cache_shared import cache
from extensions import db
from models import Categories

@cache.cached(timeout=3600, key_prefix='nav_categories')
def get_nav_categories():
    """Retrieve parent and child category nodes for navigation context."""
    try:
        # Fetch all active categories ordered by parent_id and sort_order
        query = db.select(Categories).filter_by(is_active=1).order_by(Categories.parent_id, Categories.sort_order)
        all_cats_objs = db.session.scalars(query).all()
        
        # Convert objects to dictionaries so we can assign 'subcategories' without raising TypeError
        all_cats = [
            {'id': c.id, 'name': c.name, 'slug': c.slug, 'parent_id': c.parent_id} 
            for c in all_cats_objs
        ]

        parents = [c for c in all_cats if c['parent_id'] is None]
        children = [c for c in all_cats if c['parent_id'] is not None]

        for p in parents:
            p['subcategories'] = [c for c in children if c['parent_id'] == p['id']]

        return parents
    except Exception as e:
        print(f"Error fetching nav categories via ORM: {e}")
        return []

def get_wishlist_product_ids():
    """Retrieve product IDs in the user's active wishlist."""
    from flask import has_request_context
    if not has_request_context():
        return []
    try:
        uid = session.get('user_id')
        gid = (session.get('guest_id') or request.cookies.get('guest_id')) if not uid else None
        if not uid and not gid:
            return []
        from extensions import db
        from sqlalchemy import text # type: ignore
        if uid:
            res = db.session.execute(text("""
                SELECT wi.product_id FROM wishlist_items wi 
                JOIN wishlists w ON wi.wishlist_id = w.id 
                WHERE w.user_id = :id
            """), {'id': uid}).fetchall()
        else:
            res = db.session.execute(text("""
                SELECT wi.product_id FROM wishlist_items wi 
                JOIN wishlists w ON wi.wishlist_id = w.id 
                WHERE w.session_id = :id
            """), {'id': gid}).fetchall()
        ids = [row.product_id for row in res]
        return ids
    except Exception:
        return []

@cache.cached(timeout=3600, key_prefix='company_info')
def get_company_info():
    """Retrieve global company info from the database."""
    from flask import has_app_context
    if not has_app_context():
        return None
    try:
        from models import CompanyInfo
        from extensions import db
        return db.session.scalars(db.select(CompanyInfo).limit(1)).first()
    except Exception as e:
        print(f"Error fetching company info via ORM: {e}")
        return None
