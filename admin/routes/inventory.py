import os
from io import BytesIO
from datetime import datetime
from decimal import Decimal
from PIL import Image
from extensions import db
from models import Products, Categories, ProductImages
from sqlalchemy import or_

from flask import render_template, request, redirect, url_for, flash, session, jsonify, current_app, send_file
from werkzeug.utils import secure_filename
from admin.admin_app import (
    admin_bp, admin_login_required, ProductForm, 
    CategoryForm, allowed_file, generate_unique_thumbnail_name, create_thumbnail,
    add_watermark_to_image, PRODUCT_IMAGE_DIR, THUMBNAIL_DIR,
    DEFAULT_IMAGE, get_categories_for_choices, create_global_thumbnail
)
from config_manager import get_config
from openpyxl import Workbook  # type: ignore
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side  # type: ignore
from utils.video_helpers import allowed_video_file, convert_video_to_webp

@admin_bp.route('/products')
@admin_login_required
def admin_products():
    page = request.args.get('page', 1, type=int)
    per_page = 12
    search = request.args.get('search', '')
    category = request.args.get('category', 'all')
    status = request.args.get('status', 'all')
    sort_by = request.args.get('sort', 'created_at')
    order = request.args.get('order', 'desc')

    try:
        from sqlalchemy import or_  # type: ignore

        query = db.select(Products)

        if status == 'active':
            query = query.filter(Products.is_active == True)
        elif status == 'inactive':
            query = query.filter(Products.is_active == False)
        elif status == 'lowstock':
            query = query.filter(Products.stock_quantity <= Products.reorder_level, Products.stock_quantity > 0)
        elif status == 'outofstock':
            query = query.filter(Products.stock_quantity <= 0)

        if search:
            search_term = f"%{search}%"
            query = query.filter(or_(Products.name.ilike(search_term), Products.sku.ilike(search_term)))

        if category and category != 'all':
            query = query.filter(Products.category == category)

        if sort_by == 'price':
            query = query.order_by(Products.price.desc() if order == 'desc' else Products.price.asc())
        elif sort_by == 'stock':
            query = query.order_by(Products.stock_quantity.desc() if order == 'desc' else Products.stock_quantity.asc())
        elif sort_by == 'name':
            query = query.order_by(Products.name.desc() if order == 'desc' else Products.name.asc())
        elif sort_by == 'sku':
            query = query.order_by(Products.sku.desc() if order == 'desc' else Products.sku.asc())
        else:
            query = query.order_by(Products.created_at.desc() if order == 'desc' else Products.created_at.asc())

        # Pagination logic
        total_items = db.session.execute(db.select(db.func.count()).select_from(query.subquery())).scalar()
        
        offset = (page - 1) * per_page
        query = query.limit(per_page).offset(offset)
        
        products = db.session.scalars(query).all()
        categories = db.session.scalars(db.select(Categories).order_by(Categories.name)).all()

        category_map = {str(c.id): c.name for c in categories}

        class Pagination:
            def __init__(self, page, per_page, total):
                self.page = page
                self.per_page = per_page
                self.total = total
                self.pages = (total + per_page - 1) // per_page

            def iter_pages(self, left_edge=2, left_current=2, right_current=5, right_edge=2):
                last = 0
                for num in range(1, self.pages + 1):
                    if num <= left_edge or \
                       (num > self.page - left_current - 1 and num < self.page + right_current) or \
                       num > self.pages - right_edge:
                        if last + 1 != num:
                            yield None
                        yield num
                        last = num

        pagination = Pagination(page, per_page, total_items)

        return render_template('admin/products.html',
                             products=products,
                             categories=categories,
                             category_map=category_map,
                             search=search,
                             category=category,
                             status=status,
                             sort=sort_by,
                             order=order,
                             pagination=pagination)
    except Exception as e:
        print(f"Error fetching products: {e}")
        flash('Error fetching products', 'danger')
        return render_template('admin/products.html', products=[], categories=[], pagination=None)

@admin_bp.route('/products/download')
@admin_login_required
def download_products_report():
    search = request.args.get('search', '')
    category = request.args.get('category', '')

    try:
        from sqlalchemy import or_  # type: ignore
        import csv
        from io import StringIO
        from flask import Response

        query = db.select(Products).filter(Products.is_active == True)

        if search:
            search_term = f"%{search}%"
            query = query.filter(or_(Products.name.ilike(search_term), Products.sku.ilike(search_term)))

        if category and category != 'all':
            query = query.filter(Products.category == category)

        query = query.order_by(Products.created_at.desc())
        products = db.session.scalars(query).all()

        categories = db.session.scalars(db.select(Categories)).all()
        category_map = {str(c.id): c.name for c in categories}

        si = StringIO()
        cw = csv.writer(si)
        cw.writerow(['ID', 'SKU', 'Name', 'Category', 'Price', 'Stock Quantity', 'Created At'])

        for p in products:
            cat_name = category_map.get(str(p.category), p.category) if p.category else ''
            cw.writerow([
                p.id,
                p.sku,
                p.name,
                cat_name,
                p.price,
                p.stock_quantity,
                p.created_at.strftime('%Y-%m-%d %H:%M:%S') if p.created_at else ''
            ])

        output = si.getvalue()
        si.close()

        return Response(
            output,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment;filename=products_report.csv"}
        )

    except Exception as e:
        print(f"Error generating products report: {e}")
        flash('Error generating report', 'danger')
        return redirect(url_for('admin_bp.admin_products'))

@admin_bp.route('/products/update_stock', methods=['POST'])
@admin_login_required
def update_stock():
    try:
        product_id = request.form.get('product_id')
        new_stock = request.form.get('stock')

        if not product_id or new_stock is None:
            return jsonify({'success': False, 'message': 'Missing data'}), 400
        from sqlalchemy import text  # type: ignore
        product = db.session.scalars(db.select(Products).filter_by(id=product_id)).first()
        if not product:
            return jsonify({'success': False, 'message': 'Product not found'}), 404

        previous_quantity = product.stock_quantity
        new_quantity = int(new_stock)
        adjustment = new_quantity - previous_quantity

        product.stock_quantity = new_quantity

        db.session.execute(text("""
            INSERT INTO inventory_logs
            (product_id, previous_quantity, adjustment, new_quantity,
             notes, adjusted_by, adjustment_type)
            VALUES (:pid, :prev, :adj, :new_q, :notes, :by, :type)
        """), {
            'pid': product_id, 'prev': previous_quantity, 'adj': adjustment, 'new_q': new_quantity,
            'notes': f"Quick stock update in product list",
            'by': session.get('admin_username') or 'admin', 'type': 'manual'
        })

        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/products/add', methods=['GET', 'POST'])
@admin_login_required
def add_product():
    form = ProductForm()
    form.category.choices = get_categories_for_choices()

    def set_defaults_for_new_product():
        if form.stock_quantity.data is None or form.stock_quantity.data == 0:
            form.stock_quantity.data = 1
        if form.reorder_level.data is None or form.reorder_level.data == 0:
            form.reorder_level.data = 1
        if form.mrp.data is None or form.mrp.data == 0:
            form.mrp.data = form.unit_price.data
        if form.material_cost.data is None or form.material_cost.data == 0:
            form.material_cost.data = form.unit_price.data
        if form.item_height.data is None:
            form.item_height.data = Decimal('0')
        if form.item_width.data is None:
            form.item_width.data = Decimal('0')
        if form.item_length.data is None:
            form.item_length.data = Decimal('0')
        if form.item_weight.data is None:
            form.item_weight.data = Decimal('0')
        if not form.sku.data:
            form.sku.data = '0'
        if not form.hsn_code.data:
            form.hsn_code.data = '71171990'
        if not form.size.data:
            form.size.data = 'S'
        if not form.color.data:
            form.color.data = 'Multicolor'

    if request.method == 'GET':
        set_defaults_for_new_product()

    if request.method == 'POST':
        set_defaults_for_new_product()

    if request.method == 'POST' and form.validate():
        image = DEFAULT_IMAGE

        if 'image' in request.files:
            file = request.files['image']
            if file.filename != '' and allowed_file(file.filename):
                try:
                    with Image.open(file.stream) as img:
                        img.verify()
                    file.stream.seek(0)
                except Exception as ve:
                    flash(f"Uploaded file failed image verification: {ve}", "error")
                    file = None
                
                filename = ''

                
                if file:
                    filename = secure_filename(file.filename) if file.filename else ''
                counter = 1
                name, ext = os.path.splitext(filename)
                while os.path.exists(os.path.join(current_app.root_path, PRODUCT_IMAGE_DIR, filename)):
                    filename = f"{name}-{counter}{ext}"
                    counter += 1

                image_path = os.path.join(PRODUCT_IMAGE_DIR, filename)
                abs_path = os.path.join(current_app.root_path, image_path)
                file.save(abs_path) # type: ignore

                if form.apply_watermark.data:
                    add_watermark_to_image(abs_path)

                create_thumbnail(abs_path, 'thumb_' + os.path.basename(abs_path))
                image = filename

        taxable_amount = (form.unit_price.data or Decimal(0)) # type: ignore

        if form.stock_quantity.data is None or form.stock_quantity.data == 0:
            form.stock_quantity.data = 1
        if form.reorder_level.data is None or form.reorder_level.data == 0:
            form.reorder_level.data = 1
        if form.mrp.data is None or form.mrp.data == 0:
            form.mrp.data = form.unit_price.data
        if form.material_cost.data is None or form.material_cost.data == 0:
            form.material_cost.data = form.unit_price.data
        if form.item_height.data is None:
            form.item_height.data = Decimal('0')
        if form.item_width.data is None:
            form.item_width.data = Decimal('0')
        if form.item_length.data is None:
            form.item_length.data = Decimal('0')
        if form.item_weight.data is None:
            form.item_weight.data = Decimal('0')
        if not form.sku.data:
            form.sku.data = '0'
        if not form.hsn_code.data:
            form.hsn_code.data = '71171990'
        if not form.size.data:
            form.size.data = 'S'
        if not form.color.data:
            form.color.data = 'Multicolor'

        try:
            
            sku = (form.sku.data.strip() if form.sku.data else '')
            if sku:
                existing = db.session.scalars(db.select(Products).filter_by(sku=sku)).first()
                if existing:
                    form.sku.errors.append('SKU already exists. Please use a unique SKU.') # type: ignore
                    return render_template('admin/add_product.html', form=form, product_images=[], max_images=5)

            is_active = True if form.is_active.data == '1' else False
            
            new_product = Products(
                name=form.name.data,
                description=form.description.data,
                product_features=form.product_features.data,
                care_instructions=form.care_instructions.data,
                meta_title=form.meta_title.data if form.meta_title.data else form.name.data,
                meta_keywords=form.meta_keywords.data if form.meta_keywords.data else form.name.data,
                meta_description=form.meta_description.data if form.meta_description.data else form.name.data,
                price=float(form.unit_price.data or 0),
                mrp=float(form.mrp.data or 0) if form.mrp.data else None,
                sku=sku,
                sku_variant=form.sku_variant.data,
                hsn_code=form.hsn_code.data,
                size=form.size.data,
                color=form.color.data,
                item_height=float(form.item_height.data) if form.item_height.data else None,
                item_width=float(form.item_width.data) if form.item_width.data else None,
                item_length=float(form.item_length.data) if form.item_length.data else None,
                item_weight=float(form.item_weight.data) if form.item_weight.data else None,
                material_cost=float(form.material_cost.data) if form.material_cost.data else None,
                category=form.category.data,
                material=form.material.data,
                stock_quantity=(form.stock_quantity.data or 0),
                reorder_level=(form.reorder_level.data or 0),
                is_active=is_active,
                image=image,
                gst_rate=form.gst_rate.data
            )
            db.session.add(new_product)
            db.session.flush() # To get the ID
            product_id = new_product.id

            slots = ['front_view', 'sample', 'closeup', 'size_view', 'video', 'extra_1', 'extra_2']
            product_folder = os.path.join(current_app.root_path, PRODUCT_IMAGE_DIR, str(product_id))
            os.makedirs(product_folder, exist_ok=True)

            for sort_order, slot in enumerate(slots):
                file = request.files.get(f'file_{slot}')
                is_video_slot = (slot == 'video')
                is_valid = False
                if file and file.filename != '':
                    if is_video_slot:
                        is_valid = allowed_video_file(file.filename)
                    else:
                        is_valid = allowed_file(file.filename)

                if is_valid and file:
                    filename = secure_filename(file.filename) if file.filename else ''
                    name, ext = os.path.splitext(filename)
                    counter = 1
                    while os.path.exists(os.path.join(product_folder, filename)):
                        filename = f"{name}-{counter}{ext}"
                        counter += 1
                    abs_path = os.path.join(product_folder, filename)
                    file.save(abs_path) # type: ignore

                    if is_video_slot:
                        webp_filename = os.path.splitext(filename)[0] + '.webp'
                        counter = 1
                        while os.path.exists(os.path.join(product_folder, webp_filename)):
                            webp_filename = f"{os.path.splitext(filename)[0]}-{counter}.webp"
                            counter += 1
                        webp_abs_path = os.path.join(product_folder, webp_filename)
                        try:
                            convert_video_to_webp(abs_path, webp_abs_path)
                            os.remove(abs_path)
                            filename = webp_filename
                            abs_path = webp_abs_path
                        except Exception as ve:
                            print(f"Error converting video: {ve}")
                            try: os.remove(abs_path)
                            except: pass
                            continue
                    else:
                        if form.apply_watermark.data:
                            add_watermark_to_image(abs_path)

                        if slot == 'closeup':
                            ext = os.path.splitext(filename)[1]
                            unique_thumb_name = generate_unique_thumbnail_name(
                                product_id,
                                sku,
                                ext
                            )
                            create_global_thumbnail(abs_path, unique_thumb_name)
                            new_product.image = unique_thumb_name
                        
                    relative_path = f"products/{product_id}/{filename}"
                    p_img = ProductImages(
                        product_id=product_id,
                        image_filename=relative_path,
                        image_type=slot,
                        sort_order=sort_order
                    )
                    db.session.add(p_img)

            db.session.commit()
            flash('Product added successfully', 'success')
            return redirect(url_for('admin_bp.admin_products'))

        except Exception as err:
            db.session.rollback()
            print(f"Database error: {err}")
            flash('Error adding product', 'danger')

    return render_template('admin/add_product.html', form=form, product_images=[], max_images=5)

@admin_bp.route('/products/variant/<int:product_id>', methods=['GET', 'POST'])
@admin_login_required
def add_product_variant(product_id):
    form = ProductForm()
    
    try:
        
        source = db.session.scalars(db.select(Products).filter_by(id=product_id)).first()

        if not source:
            flash('Source product not found', 'danger')
            return redirect(url_for('admin_bp.admin_products'))

        product_images = []

        form = ProductForm(request.form if request.method == 'POST' else None)
        form.category.choices = get_categories_for_choices()
        form.is_edit = False # type: ignore

        if request.method == 'GET':
            form.name.data = source.name
            form.description.data = source.description
            form.product_features.data = source.product_features
            form.care_instructions.data = source.care_instructions
            form.meta_title.data = source.meta_title
            form.meta_keywords.data = source.meta_keywords
            form.meta_description.data = source.meta_description
            form.unit_price.data = Decimal(str(source.price))
            form.mrp.data = Decimal(str(source.mrp)) if source.mrp else None
            form.hsn_code.data = source.hsn_code
            form.size.data = source.size
            form.sku.data = source.sku
            form.sku_variant.data = source.sku_variant or source.sku
            form.color.data = source.color
            form.item_height.data = Decimal(str(source.item_height)) if source.item_height is not None else Decimal('0')
            form.item_width.data = Decimal(str(source.item_width)) if source.item_width is not None else Decimal('0')
            form.item_length.data = Decimal(str(source.item_length)) if source.item_length is not None else Decimal('0')
            form.item_weight.data = Decimal(str(source.item_weight)) if source.item_weight is not None else Decimal('0')
            form.material_cost.data = Decimal(str(source.material_cost)) if source.material_cost else None
            form.category.data = source.category
            form.material.data = source.material
            form.stock_quantity.data = source.stock_quantity
            form.reorder_level.data = source.reorder_level
            form.is_active.data = '1' if source.is_active else '0'
            form.gst_rate.data = str(int(source.gst_rate)) if source.gst_rate is not None else '3'

        if request.method == 'POST' and form.validate():
            sku = (form.sku.data.strip() if form.sku.data else '')
            if db.session.scalars(db.select(Products).filter_by(sku=sku)).first():
                form.sku.errors.append('SKU already exists. Please use a unique SKU.') # type: ignore
                return render_template('admin/add_product.html', form=form, product=source, product_images=product_images, max_images=5, is_variant=True)

            image = DEFAULT_IMAGE

            if 'image' in request.files:
                file = request.files['image']
                if file.filename != '' and allowed_file(file.filename):
                    try:
                        with Image.open(file.stream) as img:
                            img.verify()
                        file.stream.seek(0)
                    except Exception as ve:
                        flash(f"Uploaded file failed image verification: {ve}", "error")
                        file = None
                    
                    filename = ''

                    
                    if file:
                        filename = secure_filename(file.filename) if file.filename else ''
                    counter = 1
                    name, ext = os.path.splitext(filename)
                    while os.path.exists(os.path.join(current_app.root_path, PRODUCT_IMAGE_DIR, filename)):
                        filename = f"{name}-{counter}{ext}"
                        counter += 1

                    image_path = os.path.join(PRODUCT_IMAGE_DIR, filename)
                    abs_path = os.path.join(current_app.root_path, image_path)
                    file.save(abs_path) # type: ignore

                    if form.apply_watermark.data:
                        add_watermark_to_image(abs_path)

                    create_thumbnail(abs_path, 'thumb_' + os.path.basename(abs_path))
                    image = filename

            is_active = True if form.is_active.data == '1' else False
            
            new_product = Products(
                name=form.name.data,
                description=form.description.data,
                product_features=form.product_features.data,
                care_instructions=form.care_instructions.data,
                meta_title=form.meta_title.data if form.meta_title.data else form.name.data,
                meta_keywords=form.meta_keywords.data if form.meta_keywords.data else form.name.data,
                meta_description=form.meta_description.data if form.meta_description.data else form.name.data,
                price=float(form.unit_price.data or 0),
                mrp=float(form.mrp.data or 0) if form.mrp.data else None,
                sku=sku,
                sku_variant=form.sku_variant.data,
                hsn_code=form.hsn_code.data,
                size=form.size.data,
                color=form.color.data,
                item_height=float(form.item_height.data) if form.item_height.data else None,
                item_width=float(form.item_width.data) if form.item_width.data else None,
                item_length=float(form.item_length.data) if form.item_length.data else None,
                item_weight=float(form.item_weight.data) if form.item_weight.data else None,
                material_cost=float(form.material_cost.data) if form.material_cost.data else None,
                category=form.category.data,
                material=form.material.data,
                stock_quantity=(form.stock_quantity.data or 0),
                reorder_level=(form.reorder_level.data or 0),
                is_active=is_active,
                image=image,
                gst_rate=form.gst_rate.data
            )
            db.session.add(new_product)
            db.session.flush()
            new_product_id = new_product.id

            slots = ['front_view', 'sample', 'closeup', 'size_view', 'video', 'extra_1', 'extra_2']
            product_folder = os.path.join(current_app.root_path, PRODUCT_IMAGE_DIR, str(new_product_id))
            
            for sort_order, slot in enumerate(slots):
                file = request.files.get(f'file_{slot}')
                is_video_slot = (slot == 'video')
                is_valid = False
                if file and file.filename != '':
                    if is_video_slot:
                        is_valid = allowed_video_file(file.filename)
                    else:
                        is_valid = allowed_file(file.filename)

                if is_valid and file:
                    os.makedirs(product_folder, exist_ok=True)
                    filename = secure_filename(file.filename) if file.filename else ''
                    name, ext = os.path.splitext(filename)
                    counter = 1
                    while os.path.exists(os.path.join(product_folder, filename)):
                        filename = f"{name}-{counter}{ext}"
                        counter += 1
                    abs_path = os.path.join(product_folder, filename)
                    file.save(abs_path) # type: ignore

                    if is_video_slot:
                        webp_filename = os.path.splitext(filename)[0] + '.webp'
                        counter = 1
                        while os.path.exists(os.path.join(product_folder, webp_filename)):
                            webp_filename = f"{os.path.splitext(filename)[0]}-{counter}.webp"
                            counter += 1
                        webp_abs_path = os.path.join(product_folder, webp_filename)
                        try:
                            convert_video_to_webp(abs_path, webp_abs_path)
                            os.remove(abs_path)
                            filename = webp_filename
                            abs_path = webp_abs_path
                        except Exception as ve:
                            print(f"Error converting video: {ve}")
                            try: os.remove(abs_path)
                            except: pass
                            continue
                    else:
                        if form.apply_watermark.data:
                            add_watermark_to_image(abs_path)

                        if slot == 'closeup':
                            ext = os.path.splitext(filename)[1]
                            unique_thumb_name = generate_unique_thumbnail_name(
                                new_product_id,
                                sku,
                                ext
                            )
                            create_global_thumbnail(abs_path, unique_thumb_name)
                            new_product.image = unique_thumb_name

                    p_img = ProductImages(
                        product_id=new_product_id,
                        image_filename=f"products/{new_product_id}/{filename}",
                        image_type=slot,
                        sort_order=sort_order
                    )
                    db.session.add(p_img)

            db.session.commit()
            flash('Variant added as a new product successfully', 'success')
            return redirect(url_for('admin_bp.admin_products'))

        return render_template('admin/add_product.html', form=form, product=source, product_images=product_images, max_images=5, is_variant=True)

    except Exception as err:
        db.session.rollback()
        print(f"Database error in add_product_variant: {err}")
        flash('Error adding product variant', 'danger')
        return redirect(url_for('admin_bp.admin_products'))

@admin_bp.route('/products/edit/<int:product_id>', methods=['GET', 'POST'])
@admin_login_required
def edit_product(product_id):
    try:
        from sqlalchemy import text  # type: ignore
        
        product = db.session.scalars(db.select(Products).filter_by(id=product_id)).first()

        if not product:
            flash('Product not found', 'danger')
            return redirect(url_for('admin_bp.admin_products'))

        # ----- 1. Fetch existing product images -----
        product_images_objs = db.session.scalars(db.select(ProductImages).filter_by(product_id=product_id).order_by(ProductImages.sort_order)).all()
        product_images = []
        for img_obj in product_images_objs:
            img_dict = {
                'id': img_obj.id,
                'image_filename': img_obj.image_filename,
                'image_type': img_obj.image_type,
                'sort_order': img_obj.sort_order,
                'image_url': url_for('static', filename=f'img/{img_obj.image_filename}')
            }
            product_images.append(img_dict)

        form = ProductForm(request.form if request.method == 'POST' else None)
        form.category.choices = get_categories_for_choices()

        if request.method == 'GET':
            form.name.data = product.name
            form.description.data = product.description
            form.product_features.data = product.product_features
            form.care_instructions.data = product.care_instructions
            form.meta_title.data = product.meta_title
            form.meta_keywords.data = product.meta_keywords
            form.meta_description.data = product.meta_description
            form.unit_price.data = Decimal(str(product.price))
            form.mrp.data = Decimal(str(product.mrp)) if product.mrp else None
            form.sku.data = product.sku
            form.sku_variant.data = product.sku_variant
            form.hsn_code.data = product.hsn_code
            form.size.data = product.size
            form.color.data = product.color
            form.item_height.data = Decimal(str(product.item_height)) if product.item_height is not None else Decimal('0')
            form.item_width.data = Decimal(str(product.item_width)) if product.item_width is not None else Decimal('0')
            form.item_length.data = Decimal(str(product.item_length)) if product.item_length is not None else Decimal('0')
            form.item_weight.data = Decimal(str(product.item_weight)) if product.item_weight is not None else Decimal('0')
            form.material_cost.data = Decimal(str(product.material_cost)) if product.material_cost else None
            form.category.data = product.category
            form.material.data = product.material
            form.stock_quantity.data = product.stock_quantity
            form.reorder_level.data = product.reorder_level
            form.is_active.data = '1' if product.is_active else '0'
            form.gst_rate.data = str(int(product.gst_rate)) if product.gst_rate is not None else '5'

        current_color = product.color
        color_choices = get_config('COLOR_CHOICES', [])
        if current_color and current_color not in dict(color_choices).values(): # type: ignore
            form.color.choices = list(color_choices) + [(current_color, current_color)] # type: ignore

        current_size = product.size
        size_choices = get_config('SIZE_CHOICES', [])
        if current_size and current_size not in dict(size_choices).values(): # type: ignore
            form.size.choices = list(size_choices) + [(current_size, current_size)] # type: ignore

        if request.method == 'POST' and form.validate():
            sku = (form.sku.data.strip() if form.sku.data else '')
            if sku:
                existing_sku = db.session.scalars(db.select(Products).filter(Products.sku == sku, Products.id != product_id)).first()
                if existing_sku:
                    form.sku.errors.append('SKU already exists. Please use a unique SKU.') # type: ignore
                    return render_template('admin/add_product.html', form=form, product=product, product_images=product_images, max_images=5)

            image = product.image

            if 'image' in request.files:
                file = request.files['image']
                if file.filename != '' and allowed_file(file.filename):
                    try:
                        with Image.open(file.stream) as img:
                            img.verify()
                        file.stream.seek(0)
                    except Exception as ve:
                        flash(f"Uploaded file failed image verification: {ve}", "error")
                        file = None
                    
                    if file:
                        if image and image != DEFAULT_IMAGE:
                            old_image_path = os.path.join(current_app.root_path, PRODUCT_IMAGE_DIR, image)
                            old_thumb_path = os.path.join(current_app.root_path, THUMBNAIL_DIR, image)
                            try:
                                if os.path.exists(old_image_path):
                                    os.remove(old_image_path)
                                if os.path.exists(old_thumb_path):
                                    os.remove(old_thumb_path)
                            except Exception as e:
                                print(f"Error removing old images: {e}")

                        ext = file.filename.rsplit('.', 1)[1].lower() if file.filename else ''
                        filename = secure_filename(file.filename) if file.filename else ''

                        abs_path = os.path.join(current_app.root_path, PRODUCT_IMAGE_DIR, filename)
                        file.save(abs_path) # type: ignore

                        if form.apply_watermark.data:
                            add_watermark_to_image(abs_path)

                        create_thumbnail(abs_path, 'thumb_' + os.path.basename(abs_path))
                        image = filename

            product.name = form.name.data
            product.description = form.description.data
            product.product_features = form.product_features.data
            product.care_instructions = form.care_instructions.data
            product.meta_title = form.meta_title.data if form.meta_title.data else form.name.data
            product.meta_keywords = form.meta_keywords.data if form.meta_keywords.data else form.name.data
            product.meta_description = form.meta_description.data if form.meta_description.data else form.name.data
            product.price = float(form.unit_price.data or 0)
            product.mrp = float(form.mrp.data or 0) if form.mrp.data else None
            product.sku = form.sku.data
            product.sku_variant = form.sku_variant.data
            product.hsn_code = form.hsn_code.data
            product.size = form.size.data
            product.color = form.color.data
            product.item_height = float(form.item_height.data) if form.item_height.data else None
            product.item_width = float(form.item_width.data) if form.item_width.data else None
            product.item_length = float(form.item_length.data) if form.item_length.data else None
            product.item_weight = float(form.item_weight.data) if form.item_weight.data else None
            product.material_cost = float(form.material_cost.data) if form.material_cost.data else None
            product.category = form.category.data
            product.material = form.material.data
            product.stock_quantity = (form.stock_quantity.data or 0)
            product.reorder_level = (form.reorder_level.data or 0)
            product.is_active = True if form.is_active.data == '1' else False
            if image != product.image:
                product.image = image
            product.gst_rate = form.gst_rate.data
            product.updated_at = db.func.now()

            slots = ['front_view', 'sample', 'closeup', 'size_view', 'video', 'extra_1', 'extra_2']
            product_folder = os.path.join(current_app.root_path, PRODUCT_IMAGE_DIR, str(product_id))
            os.makedirs(product_folder, exist_ok=True)

            for sort_order, slot in enumerate(slots):
                file = request.files.get(f'file_{slot}')
                existing_id = request.form.get(f'existing_image_id_{slot}')
                is_video_slot = (slot == 'video')

                if existing_id and existing_id.isdigit():
                    existing_id = int(existing_id)
                    p_img = db.session.scalars(db.select(ProductImages).filter_by(id=existing_id)).first()
                    
                    if file and file.filename != '':
                        is_valid = allowed_video_file(file.filename) if is_video_slot else allowed_file(file.filename)
                        if not is_valid:
                            continue

                        if p_img and p_img.image_filename:
                            old_path = os.path.join(current_app.root_path, 'static', 'img', p_img.image_filename)
                            old_thumb = os.path.join(current_app.root_path, 'static', 'img', 'thumbs',
                                                     os.path.basename(p_img.image_filename))
                            for p in [old_path, old_thumb]:
                                if os.path.exists(p):
                                    try: os.remove(p)
                                    except: pass

                        filename = secure_filename(file.filename) if file.filename else ''
                        name, ext = os.path.splitext(filename)
                        counter = 1
                        while os.path.exists(os.path.join(product_folder, filename)):
                            filename = f"{name}-{counter}{ext}"
                            counter += 1
                        abs_path = os.path.join(product_folder, filename)
                        file.save(abs_path) # type: ignore

                        if is_video_slot:
                            webp_filename = os.path.splitext(filename)[0] + '.webp'
                            counter = 1
                            while os.path.exists(os.path.join(product_folder, webp_filename)):
                                webp_filename = f"{os.path.splitext(filename)[0]}-{counter}.webp"
                                counter += 1
                            webp_abs_path = os.path.join(product_folder, webp_filename)
                            try:
                                convert_video_to_webp(abs_path, webp_abs_path)
                                os.remove(abs_path)
                                filename = webp_filename
                                abs_path = webp_abs_path
                            except Exception as ve:
                                print(f"Error converting video: {ve}")
                                try: os.remove(abs_path)
                                except: pass
                                continue
                        else:
                            if form.apply_watermark.data:
                                add_watermark_to_image(abs_path)

                            if slot == 'closeup':
                                old_thumb = product.image

                                ext = os.path.splitext(filename)[1]
                                sku_value = form.sku.data or product.sku
                                unique_thumb_name = generate_unique_thumbnail_name(product_id, sku_value, ext)

                                create_global_thumbnail(abs_path, unique_thumb_name)
                                product.image = unique_thumb_name

                                if old_thumb and old_thumb != unique_thumb_name:
                                    old_thumb_path = os.path.join(current_app.root_path, THUMBNAIL_DIR, old_thumb)
                                    if os.path.exists(old_thumb_path):
                                        try: os.remove(old_thumb_path)
                                        except: pass

                        relative_path = f"products/{product_id}/{filename}"
                        if p_img:
                            p_img.image_filename = relative_path
                            p_img.image_type = slot
                            p_img.sort_order = sort_order
                            p_img.updated_at = db.func.now()
                    else:
                        if p_img:
                            p_img.image_type = slot
                            p_img.sort_order = sort_order
                            p_img.updated_at = db.func.now()

                elif file and file.filename != '':
                    is_valid = allowed_video_file(file.filename) if is_video_slot else allowed_file(file.filename)
                    if not is_valid:
                        continue

                    filename = secure_filename(file.filename) if file.filename else ''
                    name, ext = os.path.splitext(filename)
                    counter = 1
                    while os.path.exists(os.path.join(product_folder, filename)):
                        filename = f"{name}-{counter}{ext}"
                        counter += 1
                    abs_path = os.path.join(product_folder, filename)
                    file.save(abs_path) # type: ignore

                    if is_video_slot:
                        webp_filename = os.path.splitext(filename)[0] + '.webp'
                        counter = 1
                        while os.path.exists(os.path.join(product_folder, webp_filename)):
                            webp_filename = f"{os.path.splitext(filename)[0]}-{counter}.webp"
                            counter += 1
                        webp_abs_path = os.path.join(product_folder, webp_filename)
                        try:
                            convert_video_to_webp(abs_path, webp_abs_path)
                            os.remove(abs_path)
                            filename = webp_filename
                            abs_path = webp_abs_path
                        except Exception as ve:
                            print(f"Error converting video: {ve}")
                            try: os.remove(abs_path)
                            except: pass
                            continue
                    else:
                        if form.apply_watermark.data:
                            add_watermark_to_image(abs_path)

                        if slot == 'closeup':
                            ext = os.path.splitext(filename)[1]
                            sku_value = form.sku.data or product.sku
                            unique_thumb_name = generate_unique_thumbnail_name(product_id, sku_value, ext)

                            create_global_thumbnail(abs_path, unique_thumb_name)
                            product.image = unique_thumb_name

                    relative_path = f"products/{product_id}/{filename}"
                    p_img = ProductImages(
                        product_id=product_id,
                        image_filename=relative_path,
                        image_type=slot,
                        sort_order=sort_order
                    )
                    db.session.add(p_img)

            db.session.commit()
            flash('Product updated successfully', 'success')
            return redirect(url_for('admin_bp.admin_products'))

        return render_template('admin/add_product.html', form=form, product=product, product_images=product_images, max_images=5)
    except Exception as e:
        db.session.rollback()
        print(f"Error updating product: {e}")
        flash(f'Error updating product: {str(e)}', 'danger')
        return redirect(url_for('admin_bp.admin_products'))

@admin_bp.route('/products/delete/<int:product_id>', methods=['POST'])
@admin_login_required
def delete_product(product_id):
    try:
        import os
        from flask import current_app

        product = db.session.scalars(db.select(Products).filter_by(id=product_id)).first()
        if not product:
            return jsonify({'success': False, 'message': 'Product not found'}), 404

        images = db.session.scalars(db.select(ProductImages).filter_by(product_id=product_id)).all()
        for img in images:
            img_path = os.path.join(current_app.root_path, 'static', 'img', img.image_filename)
            if os.path.exists(img_path):
                try:
                    os.remove(img_path)
                except:
                    pass
        
        if product.image and product.image != 'default.jpg':
            thumb_path = os.path.join(current_app.root_path, 'static', 'img', 'thumbs', product.image)
            if os.path.exists(thumb_path):
                try:
                    os.remove(thumb_path)
                except:
                    pass

        db.session.delete(product)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Product deleted successfully'})
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting product: {e}")
        return jsonify({'success': False, 'message': 'Cannot delete product. It may be referenced in orders.'}), 500

@admin_bp.route('/products/set-inactive/<int:product_id>', methods=['POST'])
@admin_login_required
def set_product_inactive(product_id):
    try:
        product = db.session.scalars(db.select(Products).filter_by(id=product_id)).first()
        if not product:
            return jsonify({'success': False, 'message': 'Product not found'}), 404
            
        product.is_active = False
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/products/delete-image/<int:image_id>', methods=['POST'])
@admin_login_required
def delete_product_image(image_id):
    try:
        import os
        from flask import current_app

        image_record = db.session.scalars(db.select(ProductImages).filter_by(id=image_id)).first()
        if not image_record:
            return jsonify({'success': False, 'message': 'Image not found'}), 404

        if image_record.image_filename:
            file_path = os.path.join(current_app.root_path, 'static', 'img', image_record.image_filename)
            thumb_path = os.path.join(current_app.root_path, 'static', 'img', 'thumbs', os.path.basename(image_record.image_filename))
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                if os.path.exists(thumb_path):
                    os.remove(thumb_path)
            except Exception as e:
                print(f"File deletion warning: {e}")

        db.session.delete(image_record)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting image: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/inventory')
@admin_login_required
def admin_inventory():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    search = request.args.get('search', '').strip()
    status = request.args.get('status', 'all')
    sort = request.args.get('sort', 'name')
    order = request.args.get('order', 'asc')

    try:
        from sqlalchemy import or_  # type: ignore

        query = db.select(Products).filter(Products.is_active == True)

        if search:
            search_term = f"%{search}%"
            query = query.filter(or_(Products.name.ilike(search_term), Products.sku.ilike(search_term)))

        if status == 'low':
            query = query.filter(Products.stock_quantity <= Products.reorder_level, Products.stock_quantity > 0)
        elif status == 'out':
            query = query.filter(Products.stock_quantity <= 0)
        elif status == 'instock':
            query = query.filter(Products.stock_quantity > Products.reorder_level)

        if sort == 'stock':
            query = query.order_by(Products.stock_quantity.desc() if order == 'desc' else Products.stock_quantity.asc())
        elif sort == 'sku':
            query = query.order_by(Products.sku.desc() if order == 'desc' else Products.sku.asc())
        else:
            query = query.order_by(Products.name.desc() if order == 'desc' else Products.name.asc())

        total_items = db.session.execute(db.select(db.func.count()).select_from(query.subquery())).scalar()
        offset = (page - 1) * per_page
        query = query.limit(per_page).offset(offset)
        products = db.session.scalars(query).all()

        class Pagination:
            def __init__(self, page, per_page, total):
                self.page = page
                self.per_page = per_page
                self.total = total
                self.pages = (total + per_page - 1) // per_page

            def iter_pages(self, left_edge=2, left_current=2, right_current=5, right_edge=2):
                last = 0
                for num in range(1, self.pages + 1):
                    if num <= left_edge or \
                       (num > self.page - left_current - 1 and num < self.page + right_current) or \
                       num > self.pages - right_edge:
                        if last + 1 != num:
                            yield None
                        yield num
                        last = num

        pagination = Pagination(page, per_page, total_items)
        
        return render_template('admin/inventory.html',
                             products=products,
                             pagination=pagination,
                             search=search,
                             status=status,
                             sort=sort,
                             order=order)
    except Exception as e:
        print(f"Error fetching inventory: {e}")
        flash('Error fetching inventory data', 'danger')
        return render_template('admin/inventory.html', products=[], pagination=None)

@admin_bp.route('/inventory/adjust/<int:product_id>', methods=['POST'])
@admin_login_required
def admin_adjust_inventory(product_id):
    adjustment = request.form.get('adjustment', type=int)
    notes = request.form.get('notes', '').strip()
    adjustment_type = request.form.get('adjustment_type', 'manual')
    reference_id = request.form.get('reference_id', type=int)

    if adjustment is None:
        return jsonify({'success': False, 'message': 'Invalid adjustment value'}), 400
    if adjustment == 0:
        return jsonify({'success': False, 'message': 'Adjustment cannot be zero'}), 400
    if abs(adjustment) > 1000:
        return jsonify({'success': False, 'message': 'Adjustment too large'}), 400

    try:
        from sqlalchemy import text  # type: ignore
        
        product = db.session.scalars(db.select(Products).filter_by(id=product_id)).first()
        if not product:
            return jsonify({'success': False, 'message': 'Product not found'}), 404

        previous_quantity = product.stock_quantity
        new_quantity = max(0, previous_quantity + adjustment)
        product.stock_quantity = new_quantity
        
        if new_quantity <= product.reorder_level:
            # Note: assuming needs_restock doesn't strictly exist on models but if it does:
            try: product.needs_restock = True
            except: pass

        db.session.execute(text("""
            INSERT INTO inventory_logs
            (product_id, previous_quantity, adjustment, new_quantity, notes, adjusted_by, adjustment_type, reference_id)
            VALUES (:pid, :prev, :adj, :new_q, :notes, :by, :type, :ref)
        """), {
            'pid': product_id, 'prev': previous_quantity, 'adj': adjustment, 'new_q': new_quantity,
            'notes': notes, 'by': session.get('admin_username'), 'type': adjustment_type, 'ref': reference_id
        })

        db.session.commit()
        return jsonify({
            'success': True,
            'new_quantity': new_quantity,
            'low_stock': new_quantity <= product.reorder_level
        }), 200
    except Exception as e:
        db.session.rollback()
        print(f"Database error: {e}")
        return jsonify({'success': False, 'message': 'Database error', 'error': str(e)}), 500

@admin_bp.route('/categories')
@admin_login_required
def admin_categories():
    try:
        from sqlalchemy import text  # type: ignore
        
        query = text("""
            SELECT c.*, p.name as parent_name 
            FROM categories c 
            LEFT JOIN categories p ON c.parent_id = p.id 
            ORDER BY COALESCE(p.name, c.name), c.sort_order
        """)
        categories_res = db.session.execute(query).fetchall()
        categories = [dict(row._mapping) for row in categories_res]
    except Exception as e:
        print(f"Error fetching categories: {e}")
        categories = []
    return render_template('admin/categories.html', categories=categories)

@admin_bp.route('/categories/add', methods=['GET', 'POST'])
@admin_login_required
def add_category():
    form = CategoryForm()
    
    try:
        from sqlalchemy import text  # type: ignore
        parents_res = db.session.execute(text("SELECT id, name FROM categories WHERE parent_id IS NULL")).fetchall()
        parent_choices = [(0, 'None')]
        for p in parents_res:
            parent_choices.append((p.id, p.name))
        form.parent_id.choices = parent_choices

        if request.method == 'POST' and form.validate_on_submit():
            parent_id = form.parent_id.data if form.parent_id.data != 0 else None
            is_active = True if form.is_active.data == '1' else False
            
            db.session.execute(text("""
                INSERT INTO categories (name, slug, parent_id, sort_order, is_active)
                VALUES (:n, :s, :p, :so, :ia)
            """), {
                'n': form.name.data, 's': form.slug.data, 'p': parent_id, 
                'so': form.sort_order.data, 'ia': is_active
            })
            db.session.commit()
            flash('Category added successfully!', 'success')
            return redirect(url_for('admin_bp.admin_categories'))
    except Exception as e:
        db.session.rollback()
        print(f"Error adding category: {e}")
        flash(f'Error adding category: {e}', 'danger')

    return render_template('admin/add_category.html', form=form, title="Add Category")

@admin_bp.route('/categories/edit/<int:category_id>', methods=['GET', 'POST'])
@admin_login_required
def edit_category(category_id):
    try:
        from sqlalchemy import text  # type: ignore
        category = db.session.execute(text("SELECT * FROM categories WHERE id = :id"), {'id': category_id}).fetchone()
        if not category:
            flash('Category not found', 'danger')
            return redirect(url_for('admin_bp.admin_categories'))
            
        category_dict = dict(category._mapping)
            
        form = CategoryForm(data={
            'name': category_dict['name'],
            'slug': category_dict['slug'],
            'parent_id': category_dict['parent_id'] or 0,
            'sort_order': category_dict['sort_order'],
            'is_active': '1' if category_dict['is_active'] else '0'
        })
        
        parents_res = db.session.execute(text("SELECT id, name FROM categories WHERE parent_id IS NULL AND id != :id"), {'id': category_id}).fetchall()
        parent_choices = [(0, 'None')]
        for p in parents_res:
            parent_choices.append((p.id, p.name))
        form.parent_id.choices = parent_choices

        if request.method == 'POST' and form.validate_on_submit():
            parent_id = form.parent_id.data if form.parent_id.data != 0 else None
            is_active = True if form.is_active.data == '1' else False
            
            db.session.execute(text("""
                UPDATE categories 
                SET name = :n, slug = :s, parent_id = :p, sort_order = :so, is_active = :ia
                WHERE id = :id
            """), {
                'n': form.name.data, 's': form.slug.data, 'p': parent_id, 
                'so': form.sort_order.data, 'ia': is_active, 'id': category_id
            })
            db.session.commit()
            flash('Category updated successfully!', 'success')
            return redirect(url_for('admin_bp.admin_categories'))
            
        return render_template('admin/add_category.html', form=form, title="Edit Category", category_id=category_id)
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'danger')
        return redirect(url_for('admin_bp.admin_categories'))

@admin_bp.route('/categories/delete/<int:category_id>', methods=['POST'])
@admin_login_required
def delete_category(category_id):
    try:
        from sqlalchemy import text  # type: ignore
        count_res = db.session.execute(text("SELECT COUNT(*) FROM categories WHERE parent_id = :id"), {'id': category_id}).scalar()
        if count_res and count_res > 0:
            flash('Cannot delete category with subcategories. Delete subcategories first.', 'danger')
        else:
            db.session.execute(text("DELETE FROM categories WHERE id = :id"), {'id': category_id})
            db.session.commit()
            flash('Category deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting category: {e}', 'danger')
    return redirect(url_for('admin_bp.admin_categories'))
