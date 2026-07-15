import math
import json
import traceback
from flask import Blueprint, render_template, request, session, redirect, url_for, flash, jsonify
from database import DatabaseConnectionError, ProductRepository
from config_manager import get_config
from utils.session_helpers import get_guest_or_user_cart_count, get_wishlist_product_ids
from utils.cache_shared import cache

shop_bp = Blueprint('shop_bp', __name__)

from sqlalchemy import func  # type: ignore
from extensions import db
from models import Products, Categories, Cart, GuestCart, ProductReviews, Orders, OrderItems, Users

@cache.cached(timeout=300, key_prefix='shop_filter_counts')
def _get_shop_filter_counts():
    try:
        # Price bounds
        price_bounds_res = db.session.execute(
            db.select(func.min(Products.price).label('min_p'), func.max(Products.price).label('max_p'))
            .filter(Products.is_active == 1)
        ).first()
        price_bounds = {'min_p': price_bounds_res.min_p, 'max_p': price_bounds_res.max_p} if price_bounds_res else None

        # Material counts
        materials_res = db.session.execute(
            db.select(Products.material, func.count().label('count'))
            .filter(Products.material != None, Products.material != '', Products.is_active == 1)
            .group_by(Products.material)
        ).all()
        material_counts = {row.material: row.count for row in materials_res}

        # Color counts
        colors_res = db.session.execute(
            db.select(Products.color, func.count().label('count'))
            .filter(Products.color != None, Products.color != '', Products.is_active == 1)
            .group_by(Products.color)
        ).all()
        color_counts = {row.color: row.count for row in colors_res}

        # Category counts
        categories_res = db.session.execute(
            db.select(Categories.slug, Categories.name, func.count(Products.id).label('count'))
            .outerjoin(Products, (Products.category == Categories.id) & (Products.is_active == 1))
            .filter(Categories.is_active == 1)
            .group_by(Categories.id, Categories.slug, Categories.name)
        ).all()
        category_counts = {row.slug: row.count for row in categories_res}

        return price_bounds, material_counts, color_counts, category_counts
    except Exception as e:
        print(f"Error fetching shop filter counts: {e}")
        return None, {}, {}, {}

from sqlalchemy.sql import text, or_, and_, desc, asc  # type: ignore

@shop_bp.route("/shop")
def shop():
    try:
        cart_product_ids = []
        material_counts = {}
        color_counts = {}
        category_counts = {}
        pagination = None

        min_price = request.args.get('min_price', type=int)
        max_price = request.args.get('max_price', type=int)

        category = request.args.get('category', 'all')
        materials = request.args.getlist('material')
        colors = request.args.getlist('color')
        search_query = request.args.get('q', '').strip()
        sort_option = request.args.get('sort', 'latest')
        page = max(1, request.args.get('page', 1, type=int))
        view_param = request.args.get('view', 'grid')

        cart_product_ids = []
        if 'user_id' in session:
            cart_items = db.session.scalars(db.select(Cart.product_id).filter_by(user_id=session['user_id'])).all()
            cart_product_ids = list(cart_items)
        elif 'guest_id' in session:
            cart_items = db.session.scalars(db.select(GuestCart.product_id).filter_by(guest_id=session['guest_id'])).all()
            cart_product_ids = list(cart_items)

        price_bounds, material_counts, color_counts, category_counts = _get_shop_filter_counts()

        global_min_price = int(price_bounds['min_p']) if price_bounds and price_bounds['min_p'] is not None else 0
        global_max_price = int(price_bounds['max_p']) if price_bounds and price_bounds['max_p'] is not None else 10000

        if min_price is None:
            min_price = global_min_price
        if max_price is None:
            max_price = global_max_price

        # Subquery for review stats
        pr_stats = (
            db.select(
                ProductReviews.product_id,
                func.avg(ProductReviews.rating).label('avg_rating'),
                func.count(ProductReviews.id).label('review_count')
            )
            .group_by(ProductReviews.product_id)
            .subquery()
        )

        query = (
            db.select(
                Products.id, Products.name, func.coalesce(Products.price, 0).label('price'),
                Products.mrp, Products.image, Products.category, Products.material,
                Products.stock_quantity, Products.color, Products.description,
                Products.item_height, Products.item_length, Products.item_width,
                Products.item_weight,
                func.coalesce(pr_stats.c.avg_rating, 0).label('avg_rating'),
                func.coalesce(pr_stats.c.review_count, 0).label('review_count')
            )
            .outerjoin(pr_stats, Products.id == pr_stats.c.product_id)
            .filter(Products.is_active == 1)
        )

        query = query.filter(Products.price.between(min_price, max_price))

        if category != 'all':
            cat_obj = db.session.execute(db.select(Categories.id).filter_by(slug=category)).scalar()
            if cat_obj:
                query = query.filter(Products.category == cat_obj)
            else:
                query = query.filter(Products.category == -1)

        if materials and 'all' not in materials:
            valid_materials = set(material_counts.keys())
            materials = [m for m in materials if m in valid_materials]
            if materials:
                query = query.filter(Products.material.in_(materials))

        if colors and 'all' not in colors:
            colors = [c for c in colors if c]
            if colors:
                query = query.filter(Products.color.in_(colors))

        if search_query:
            search_terms = search_query.strip()
            if search_terms:
                words = search_terms.split()
                # Use raw SQL for MATCH AGAINST as it is specific to MySQL
                match_clause = text("MATCH(products.name, products.category, products.material, products.color, products.description) AGAINST (:search_terms IN NATURAL LANGUAGE MODE)")

                like_conditions = []
                for word in words:
                    term = f"%{word}%"
                    like_conditions.append(or_(
                        Products.name.like(term),
                        Products.description.like(term),
                        Products.sku.like(term),
                        Products.material.like(term),
                        Products.color.like(term)
                    ))
                    if len(word) > 3:
                        like_conditions.append(Products.name.op('SOUNDS LIKE')(word))

                numeric_search = search_terms.replace('₹', '').replace(',', '').strip()
                if numeric_search and numeric_search.replace('.', '', 1).isdigit():
                    like_conditions.append(func.cast(Products.price, db.String).like(f"%{numeric_search}%"))

                query = query.filter(or_(
                    match_clause.bindparams(search_terms=search_terms),
                    *like_conditions
                ))

        # Count total
        count_query = db.select(func.count()).select_from(query.subquery())
        total = db.session.scalar(count_query) or 0

        sort_options = {
            'latest': desc(Products.id),
            'price-low': asc(Products.price),
            'price-high': desc(Products.price),
            'popular': desc(Products.views),
            'rating': desc(text('avg_rating')),
        }
        
        query = query.order_by(sort_options.get(sort_option, desc(Products.id)))

        per_page = 12
        total_pages = math.ceil(total / per_page) if total > 0 else 1
        page = min(page, total_pages) if total_pages > 0 else 1
        offset = (page - 1) * per_page
        
        query = query.limit(per_page).offset(offset)
        
        products_res = db.session.execute(query).all()
        products = [dict(row._mapping) for row in products_res]

        def get_page_url(page_num):
            args = request.args.copy()
            args['page'] = page_num
            return url_for('shop_bp.shop', **args)  # type: ignore

        pagination = {
            'page': page,
            'per_page': per_page,
            'total': total,
            'pages': total_pages,
            'has_prev': page > 1,
            'has_next': page < total_pages,
            'prev_num': page - 1 if page > 1 else None,
            'next_num': page + 1 if page < total_pages else None,
            'iter_pages': lambda left_edge=2, left_current=2, right_current=5, right_edge=2: range(1, total_pages + 1),
            'get_page_url': get_page_url,
        }

        return render_template(
            "shop.html",
            products=products,
            cart_product_ids=cart_product_ids,
            pagination=pagination,
            sort_options={
                'latest': 'Latest',
                'price-low': 'Price: Low to High',
                'price-high': 'Price: High to Low',
                'popular': 'Popular',
                'rating': 'Rating'
            },
            material_counts=material_counts,
            color_counts=color_counts,
            category_counts=category_counts,
            products_total_count=total,
            user_logged_in=("user_id" in session),
            username=session.get("username"),
            current_view=view_param,
            cart_count=get_guest_or_user_cart_count(),
            global_min_price=global_min_price,
            global_max_price=global_max_price,
            selected_min_price=min_price,
            selected_max_price=max_price,
            color_map=get_config('COLOR_MAP', {})
        )
    except Exception as e:
        print(f"Error in shop route: {str(e)}")
        traceback.print_exc()
        flash('Error loading products', 'error')
        return redirect(url_for('pages_bp.home'))

@shop_bp.route('/detail/<int:product_id>')
def detail(product_id):
    try:
        product = ProductRepository.get_by_id(product_id)
        if not product:
            flash('Product not found', 'error')
            return redirect(url_for('shop_bp.shop'))

        if product.get('sku_variant'):
            parent_product = db.session.execute(
                db.select(Products.description, Products.product_features, Products.care_instructions, Products.meta_title, Products.meta_description, Products.meta_keywords)
                .filter(Products.sku_variant == product['sku_variant'], Products.is_active == 1)
                .order_by(Products.id)
            ).first()
            if parent_product:
                if not product.get('description') and parent_product.description:
                    product['description'] = parent_product.description
                if not product.get('product_features') and parent_product.product_features:
                    product['product_features'] = parent_product.product_features
                if not product.get('care_instructions') and parent_product.care_instructions:
                    product['care_instructions'] = parent_product.care_instructions
                if not product.get('meta_title') and parent_product.meta_title:
                    product['meta_title'] = parent_product.meta_title
                if not product.get('meta_description') and parent_product.meta_description:
                    product['meta_description'] = parent_product.meta_description
                if not product.get('meta_keywords') and parent_product.meta_keywords:
                    product['meta_keywords'] = parent_product.meta_keywords

        cart_product_ids = []
        if 'user_id' in session:
            cart_items = db.session.scalars(db.select(Cart.product_id).filter_by(user_id=session['user_id'])).all()
            cart_product_ids = list(cart_items)
        elif 'guest_id' in session:
            cart_items = db.session.scalars(db.select(GuestCart.product_id).filter_by(guest_id=session['guest_id'])).all()
            cart_product_ids = list(cart_items)

        product_images = ProductRepository.get_images(product_id)
        for img in product_images:
            img['url'] = url_for('static', filename='img/' + img['image_filename'])

        # Related products
        related_products_res = db.session.execute(
            db.select(Products.id, Products.name, Products.price.label('unit_price'), Products.image, Products.stock_quantity)
            .filter(Products.category == product.get('category', 'earrings'), Products.id != product_id, Products.is_active == 1)
            .limit(5)
        ).all()
        related_products = [dict(row._mapping) for row in related_products_res]

        if len(related_products) < 5:
            needed = 5 - len(related_products)
            exclude_ids = [product_id] + [p['id'] for p in related_products]
            additional_res = db.session.execute(
                db.select(Products.id, Products.name, Products.price.label('unit_price'), Products.image, Products.stock_quantity)
                .filter(Products.id.notin_(exclude_ids), Products.is_active == 1)
                .order_by(func.rand())
                .limit(needed)
            ).all()
            additional = [dict(row._mapping) for row in additional_res]
            related_products.extend(additional)

        # Review stats
        review_stats_res = db.session.execute(
            db.select(func.avg(ProductReviews.rating).label('avg_rating'), func.count(ProductReviews.id).label('review_count'))
            .filter(ProductReviews.product_id == product_id)
        ).first()
        
        avg_rating = float(review_stats_res.avg_rating) if review_stats_res and review_stats_res.avg_rating else 0.0
        review_count = review_stats_res.review_count if review_stats_res else 0

        can_review = False
        has_purchased = False
        has_reviewed_all = False
        order_id = None
        
        if 'user_id' in session:
            user_id = session['user_id']
            # Has purchased
            delivered_orders = db.session.scalars(
                db.select(Orders.id)
                .join(OrderItems, OrderItems.order_id == Orders.id)
                .filter(OrderItems.product_id == product_id, Orders.user_id == user_id, Orders.status == 'delivered')
            ).all()
            has_purchased = len(list(delivered_orders)) > 0
            
            if has_purchased:
                # Reviewable orders (not reviewed yet)
                reviewable_res = db.session.scalars(
                    db.select(Orders.id)
                    .join(OrderItems, OrderItems.order_id == Orders.id)
                    .outerjoin(ProductReviews, and_(ProductReviews.order_id == Orders.id, ProductReviews.product_id == OrderItems.product_id, ProductReviews.user_id == Orders.user_id))
                    .filter(OrderItems.product_id == product_id, Orders.user_id == user_id, Orders.status == 'delivered', ProductReviews.id == None)
                    .order_by(desc(Orders.order_date))
                ).all()
                reviewable_orders = list(reviewable_res)
                can_review = len(reviewable_orders) > 0
                has_reviewed_all = not can_review
                if can_review:
                    order_id = reviewable_orders[0]

        # Recent reviews
        recent_reviews_res = db.session.execute(
            db.select(ProductReviews.rating, ProductReviews.review_text, ProductReviews.created_at, Users.username, Users.first_name, Users.last_name, Users.profile_picture, ProductReviews.title, ProductReviews.media_files)
            .join(Users, ProductReviews.user_id == Users.id)
            .filter(ProductReviews.product_id == product_id)
            .order_by(desc(ProductReviews.created_at))
            .limit(2)
        ).all()
        recent_reviews = [dict(row._mapping) for row in recent_reviews_res]
        
        for review in recent_reviews:
            if review.get('media_files'):
                try:
                    if isinstance(review['media_files'], str):
                        review['parsed_media'] = json.loads(review['media_files'])
                    else:
                        review['parsed_media'] = review['media_files']
                except:
                    review['parsed_media'] = []
            else:
                review['parsed_media'] = []
            
            fname = review.get('first_name', '')
            lname = review.get('last_name', '')
            review['display_name'] = f"{fname} {lname}".strip() or review.get('username')

        # Variants
        all_variants_res = db.session.execute(
            db.select(Products.id, Products.color, Products.size, Products.image, Products.stock_quantity)
            .filter(Products.sku_variant == product.get('sku_variant'), Products.is_active == 1)
        ).all()
        all_variants = [dict(row._mapping) for row in all_variants_res]
        
        color_variants = []
        seen_colors = set()
        for v in all_variants:
            if v['color'] and v['color'] not in seen_colors:
                seen_colors.add(v['color'])
                color_variants.append(v)
        color_variants.sort(key=lambda x: x['color'] if x['color'] else '')

        size_options = []
        # Find variants to extract sizes from (either same color, or all if no color)
        if product.get('color'):
            variants_for_size = [v for v in all_variants if v['color'] == product['color']]
        else:
            variants_for_size = all_variants
            
        seen_sizes = set()
        for variant in variants_for_size:
            if variant['size'] and variant['size'] not in seen_sizes:
                seen_sizes.add(variant['size'])
                size_options.append({
                    'id': variant['id'],
                    'size': variant['size'],
                    'is_available': variant['stock_quantity'] > 0,
                    'is_current': variant['id'] == product['id']
                })

        size_order = ['XS', 'S', 'M', 'L', 'XL', 'XXL', 'one_size', 'adjustable', 'custom']
        size_options.sort(key=lambda x: size_order.index(x['size']) if x['size'] in size_order else 999)

        return render_template(
            "detail.html",
            product=product,
            product_images=product_images,
            related_products=related_products,
            color_variants=color_variants,
            size_options=size_options,
            avg_rating=avg_rating,
            review_count=review_count,
            recent_reviews=recent_reviews,
            user_logged_in=("user_id" in session),
            username=session.get("username"),
            cart_product_ids=cart_product_ids,
            wishlist_product_ids=get_wishlist_product_ids(),
            can_review=can_review,
            has_reviewed_all=has_reviewed_all,
            order_id=order_id,
            color_map=get_config('COLOR_MAP', {})
        )
    except Exception as e:
        print(f"Error in detail route: {str(e)}")
        traceback.print_exc()
        flash('Error loading product details', 'error')
        return redirect(url_for('pages_bp.home'))

@shop_bp.route('/product-reviews/<int:product_id>')
def product_reviews(product_id):
    try:
        product_res = db.session.execute(
            db.select(Products.name)
            .filter(Products.id == product_id, Products.is_active == 1)
        ).first()
        
        if not product_res:
            flash('Product not found', 'error')
            return redirect(url_for('shop_bp.shop'))

        reviews_res = db.session.execute(
            db.select(ProductReviews.rating, ProductReviews.review_text, ProductReviews.created_at, Users.username, Users.first_name, Users.last_name, Users.profile_picture, ProductReviews.title, ProductReviews.media_files)
            .join(Users, ProductReviews.user_id == Users.id)
            .filter(ProductReviews.product_id == product_id)
            .order_by(desc(ProductReviews.created_at))
        ).all()
        
        reviews = [dict(row._mapping) for row in reviews_res]
        
        for review in reviews:
            if review.get('media_files'):
                try:
                    if isinstance(review['media_files'], str):
                        review['parsed_media'] = json.loads(review['media_files'])
                    else:
                        review['parsed_media'] = review['media_files']
                except:
                    review['parsed_media'] = []
            else:
                review['parsed_media'] = []
            fname = review.get('first_name', '')
            lname = review.get('last_name', '')
            review['display_name'] = f"{fname} {lname}".strip() or review.get('username')

        stats_res = db.session.execute(
            db.select(func.avg(ProductReviews.rating).label('avg_rating'), func.count(ProductReviews.id).label('review_count'))
            .filter(ProductReviews.product_id == product_id)
        ).first()
        
        avg_rating = float(stats_res.avg_rating) if stats_res and stats_res.avg_rating else 0.0
        review_count = stats_res.review_count if stats_res else 0

        return render_template('product_reviews.html', product_name=product_res.name, product_id=product_id, reviews=reviews, avg_rating=avg_rating, review_count=review_count, user_logged_in='user_id' in session, username=session.get('username'))
    except Exception as e:
        print(f"Error fetching product reviews: {str(e)}")
        flash('Error loading product reviews', 'error')
        return redirect(url_for('pages_bp.home'))

@shop_bp.route('/api/search-autocomplete')
def search_autocomplete():
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({'suggestions': [], 'products': []})

    search_pattern = f"%{q}%"

    # 1. Fetch suggestions (matching categories and maybe product keywords)
    suggestions_res = db.session.execute(
        db.select(Categories.name)
        .filter(Categories.name.ilike(search_pattern), Categories.is_active == 1)
        .limit(3)
    ).scalars().all()
    suggestions = list(suggestions_res)
    
    if q.lower() not in [s.lower() for s in suggestions]:
        suggestions.insert(0, q.lower())
    suggestions = suggestions[:4]

    # 2. Fetch products with category names
    products_res = db.session.execute(
        db.select(Products, Categories.name.label('category_name'))
        .outerjoin(Categories, Products.category == Categories.id)
        .filter(Products.is_active == 1, Products.name.ilike(search_pattern))
        .limit(4)
    ).all()

    products_list = []
    for row in products_res:
        p = row.Products
        c_name = row.category_name
        
        # Determine subtitle
        subtitle = c_name
        if not subtitle:
            subtitle = p.category if p.category and not p.category.isdigit() else (p.material or 'Product')

        products_list.append({
            'id': p.id,
            'name': p.name,
            'price': float(p.price) if p.price else 0.0,
            'image_url': url_for('static', filename='img/thumbs/' + p.image) if p.image else '',
            'subtitle': subtitle
        })

    return jsonify({
        'suggestions': suggestions,
        'products': products_list
    })
