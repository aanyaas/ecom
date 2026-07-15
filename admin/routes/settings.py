import os
import json
import bcrypt
from flask import render_template, request, redirect, url_for, flash, session, jsonify, current_app
from werkzeug.utils import secure_filename
from admin.admin_app import (
    admin_bp, admin_login_required, CompanyInfoForm, 
    allowed_file
)
from config_manager import save_config, load_config
from extensions import db
from sqlalchemy import text

@admin_bp.route('/get-settings')
@admin_login_required
def get_settings():
    try:
        query = text("""
            SELECT email_notifications, theme, items_per_page
            FROM admin_settings
            WHERE admin_id = :admin_id
        """)
        settings_row = db.session.execute(query, {'admin_id': session['admin_id']}).fetchone()

        if not settings_row:
            settings = {
                'email_notifications': True,
                'theme': 'light',
                'items_per_page': 10
            }
        else:
            settings = dict(settings_row._mapping)

        session['admin_theme'] = settings.get('theme', 'light')

        return jsonify({
            'success': True,
            'settings': settings
        })
    except Exception as e:
        print(f"Error getting settings: {e}")
        return jsonify({'success': False}), 500

@admin_bp.route('/save-settings', methods=['POST'])
@admin_login_required
def save_settings():
    from extensions import db
    try:
        email_notifications = request.form.get('email_notifications') == '1'
        theme = request.form.get('theme', 'light')
        items_per_page = int(request.form.get('items_per_page', 10))

        try:
            from sqlalchemy import text
            query_check = text("""
                SELECT 1 FROM admin_settings
                WHERE admin_id = :admin_id
            """)
            exists = db.session.execute(query_check, {'admin_id': session['admin_id']}).fetchone()

            if exists:
                update_query = text("""
                    UPDATE admin_settings
                    SET email_notifications = :email_notifications,
                        theme = :theme,
                        items_per_page = :items_per_page,
                        updated_at = NOW()
                    WHERE admin_id = :admin_id
                """)
                db.session.execute(update_query, {
                    'email_notifications': email_notifications,
                    'theme': theme,
                    'items_per_page': items_per_page,
                    'admin_id': session['admin_id']
                })
            else:
                insert_query = text("""
                    INSERT INTO admin_settings
                    (admin_id, email_notifications, theme, items_per_page)
                    VALUES (:admin_id, :email_notifications, :theme, :items_per_page)
                """)
                db.session.execute(insert_query, {
                    'admin_id': session['admin_id'],
                    'email_notifications': email_notifications,
                    'theme': theme,
                    'items_per_page': items_per_page
                })

            db.session.commit()
            return jsonify({'success': True})
        except Exception as e:
            db.session.rollback()
            print(f"Error saving settings: {e}")
            return jsonify({'success': False, 'message': str(e)}), 500
    except Exception as e:
        print(f"Error processing settings: {e}")
        return jsonify({'success': False, 'message': str(e)}), 400

@admin_bp.route('/change-password', methods=['POST'])
@admin_login_required
def change_password():
    from extensions import db
    current_password = request.form.get('current_password')
    new_password = request.form.get('new_password')
    confirm_new_password = request.form.get('confirm_new_password')

    if not current_password or not new_password or not confirm_new_password:
        return jsonify({'success': False, 'message': 'All fields are required'}), 400

    if new_password != confirm_new_password:
        return jsonify({'success': False, 'message': 'New passwords do not match'}), 400

    if len(new_password) < 8:
        return jsonify({'success': False, 'message': 'Password must be at least 8 characters'}), 400

    try:
        from models import AdminUsers
        admin = db.session.scalars(db.select(AdminUsers).filter_by(id=session['admin_id'])).first()

        if not admin or not bcrypt.checkpw(current_password.encode('utf-8'), admin.password.encode('utf-8')):
            return jsonify({'success': False, 'message': 'Current password is incorrect'}), 400

        hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        admin.password = hashed_password

        db.session.commit()
        return jsonify({'success': True, 'message': 'Password changed successfully'})
    except Exception as e:
        db.session.rollback()
        print(f"Error changing password: {e}")
        return jsonify({'success': False, 'message': 'Error changing password'}), 500

@admin_bp.route('/company-info', methods=['GET', 'POST'])
@admin_login_required
def admin_company_info():
    form = CompanyInfoForm()
    
    try:
        from extensions import db
        from models import CompanyInfo, PincodeStateCity
        from sqlalchemy import text
        
        # Populate state choices
        states_res = db.session.execute(db.select(PincodeStateCity.state_name).distinct().order_by(PincodeStateCity.state_name)).scalars().all()
        form.state.choices = [('', 'Select State')] + [(state, state) for state in states_res]

        if request.method == 'POST' and form.validate():
            logo_filename = None
            if 'logo' in request.files:
                file = request.files['logo']
                if file.filename != '' and allowed_file(file.filename):
                    filename = secure_filename(file.filename or '')
                    logo_path = os.path.join(current_app.root_path, 'static', 'img', 'company', filename)
                    os.makedirs(os.path.dirname(logo_path), exist_ok=True)
                    file.save(logo_path)
                    logo_filename = filename

            existing = db.session.scalars(db.select(CompanyInfo).limit(1)).first()

            if existing:
                existing.company_name = form.company_name.data
                existing.address = form.address.data
                existing.phone = form.phone.data
                existing.email = form.email.data
                existing.gstin = form.gstin.data
                existing.pan = form.pan.data
                existing.state = form.state.data
                existing.city = form.city.data
                existing.state_code = form.state_code.data
                existing.pincode = form.pincode.data
                existing.website = form.website.data
                existing.updated_at = db.func.now()
                if logo_filename:
                    existing.logo = logo_filename
            else:
                new_info = CompanyInfo(
                    company_name=form.company_name.data,
                    address=form.address.data,
                    phone=form.phone.data,
                    email=form.email.data,
                    gstin=form.gstin.data,
                    pan=form.pan.data,
                    state=form.state.data,
                    city=form.city.data,
                    state_code=form.state_code.data,
                    pincode=form.pincode.data,
                    website=form.website.data,
                    logo=logo_filename
                )
                db.session.add(new_info)

            db.session.commit()
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': True, 'message': 'Company information saved successfully'})
            flash('Company information saved successfully', 'success')
            return redirect(url_for('admin_bp.admin_company_info'))

        company_info = db.session.scalars(db.select(CompanyInfo).limit(1)).first()

        if company_info:
            form.company_name.data = company_info.company_name
            form.address.data = company_info.address
            form.phone.data = company_info.phone
            form.email.data = company_info.email
            form.gstin.data = company_info.gstin
            form.pan.data = company_info.pan if company_info.pan else ''
            form.state.data = company_info.state if company_info.state else ''
            form.city.data = company_info.city if company_info.city else ''
            form.state_code.data = company_info.state_code if company_info.state_code else ''
            form.pincode.data = company_info.pincode if company_info.pincode else ''
            form.website.data = company_info.website if company_info.website else ''

            if form.state.data:
                cities_res = db.session.execute(db.select(PincodeStateCity.city).filter_by(state_name=form.state.data).distinct().order_by(PincodeStateCity.city)).scalars().all()
                form.city.choices = [('', 'Select City')] + [(city, city) for city in cities_res]

            logo_url = url_for('static', filename=f'img/company/{company_info.logo}') if company_info.logo else None
        else:
            logo_url = None

        return render_template('admin/company_info.html',
                             form=form,
                             logo_url=logo_url)

    except Exception as e:
        from extensions import db
        db.session.rollback()
        print(f"Error managing company info: {e}")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': 'Error saving company information'})
        flash('Error saving company information', 'danger')
        return render_template('admin/company_info.html', form=form)

@admin_bp.route('/get-state-data')
@admin_login_required
def get_state_data():
    state_name = request.args.get('state')

    try:
        from extensions import db
        from models import PincodeStateCity
        
        state_data = db.session.scalars(db.select(PincodeStateCity.state_code).filter_by(state_name=state_name).distinct().limit(1)).first()
        cities = db.session.scalars(db.select(PincodeStateCity.city).filter_by(state_name=state_name).distinct().order_by(PincodeStateCity.city)).all()

        return jsonify({
            'success': True,
            'state_code': state_data if state_data else '',
            'cities': list(cities)
        })

    except Exception as e:
        print(f"Error getting state data: {e}")
        return jsonify({'success': False, 'message': 'Error retrieving state data'}), 500

@admin_bp.route('/get-pincode-data')
@admin_login_required
def get_pincode_data():
    pincode = request.args.get('pincode')

    try:
        from extensions import db
        from models import PincodeStateCity
        
        pincode_data = db.session.scalars(db.select(PincodeStateCity).filter_by(pincode=pincode).limit(1)).first()

        if pincode_data:
            return jsonify({
                'success': True,
                'state': pincode_data.state_name,
                'city': pincode_data.city,
                'state_code': pincode_data.state_code
            })
        else:
            return jsonify({'success': False, 'message': 'Pincode not found'})

    except Exception as e:
        print(f"Error getting pincode data: {e}")
        return jsonify({'success': False, 'message': 'Error retrieving pincode data'}), 500

@admin_bp.route('/testimonials')
@admin_login_required
def admin_testimonials():
    """List all testimonials grouped by status."""
    try:
        from extensions import db
        from models import CustomerTestimonials
        
        all_t = db.session.scalars(db.select(CustomerTestimonials).order_by(CustomerTestimonials.created_at.desc())).all()
        pending  = [t for t in all_t if t.is_approved == 0]
        approved = [t for t in all_t if t.is_approved == 1]
        rejected = [t for t in all_t if t.is_approved == -1]
        return render_template('admin/testimonials.html',
                               pending=pending,
                               approved=approved,
                               rejected=rejected)
    except Exception as e:
        print(f"Error loading testimonials: {e}")
        flash('Error loading testimonials', 'danger')
        return render_template('admin/testimonials.html', pending=[], approved=[], rejected=[])

@admin_bp.route('/testimonials/<int:testimonial_id>/action', methods=['POST'])
@admin_login_required
def admin_approve_testimonial(testimonial_id):
    """Approve, reject or delete a testimonial."""
    action = request.form.get('action')
    try:
        from models import CustomerTestimonials
        
        testimonial = db.session.scalars(db.select(CustomerTestimonials).filter_by(id=testimonial_id)).first()
        if testimonial:
            if action == 'approve':
                testimonial.is_approved = 1
                flash('Testimonial approved and is now live on the website.', 'success')
            elif action == 'reject':
                testimonial.is_approved = -1
                flash('Testimonial has been rejected and hidden from the website.', 'warning')
            elif action == 'delete':
                db.session.delete(testimonial)
                flash('Testimonial permanently deleted.', 'danger')
            db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Error updating testimonial: {e}")
        flash('Error performing action. Please try again.', 'danger')
    return redirect(url_for('admin_bp.admin_testimonials'))

@admin_bp.route('/global-settings', methods=['GET', 'POST'])
def global_settings():
    # Only allow admin access
    if 'admin_id' not in session:
        return redirect(url_for('admin_bp.admin_login'))
        
    if request.method == 'POST':
        try:
            config_data = load_config()
            
            # Update Brand and Social Media
            config_data['BRAND_NAME'] = request.form.get('BRAND_NAME', 'Aanyaas')
            config_data['SOCIAL_FACEBOOK'] = request.form.get('SOCIAL_FACEBOOK', '')
            config_data['SOCIAL_INSTAGRAM'] = request.form.get('SOCIAL_INSTAGRAM', '')
            config_data['SOCIAL_YOUTUBE'] = request.form.get('SOCIAL_YOUTUBE', '')
            
            # Update simple values
            config_data['FREE_SHIPPING_THRESHOLD'] = float(request.form.get('FREE_SHIPPING_THRESHOLD', 500.00))
            config_data['DEFAULT_SHIPPING_CHARGE'] = float(request.form.get('DEFAULT_SHIPPING_CHARGE', 99.00))
            config_data['INSTAGRAM_CACHE_TIMEOUT'] = int(request.form.get('INSTAGRAM_CACHE_TIMEOUT', 3600))
            config_data['INSTAGRAM_MEDIA_COUNT'] = int(request.form.get('INSTAGRAM_MEDIA_COUNT', 6))
            
            # Parse complex JSON values
            config_data['COLOR_MAP'] = json.loads(request.form.get('COLOR_MAP', '{}'))
            config_data['COLOR_NAME_MAP'] = json.loads(request.form.get('COLOR_NAME_MAP', '{}'))
            config_data['VALID_COUPONS'] = json.loads(request.form.get('VALID_COUPONS', '{}'))
            config_data['COLOR_CHOICES'] = json.loads(request.form.get('COLOR_CHOICES', '[]'))
            config_data['SIZE_CHOICES'] = json.loads(request.form.get('SIZE_CHOICES', '[]'))
            config_data['GST_CHOICES'] = json.loads(request.form.get('GST_CHOICES', '[]'))
            config_data['MATERIAL_CHOICES'] = json.loads(request.form.get('MATERIAL_CHOICES', '[]'))
            
            if save_config(config_data):
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({'success': True, 'message': 'Global settings updated successfully.'})
                flash('Global settings updated successfully.', 'success')
            else:
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({'success': False, 'message': 'Failed to save settings.'})
                flash('Failed to save settings.', 'error')
        except Exception as e:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': str(e)})
            flash(f'Error updating settings: {e}', 'error')
            
        return redirect(url_for('admin_bp.global_settings'))
        
    config = load_config()
    return render_template('admin/global_settings.html', config=config, json=json)

@admin_bp.route('/home-settings', methods=['GET', 'POST'])
@admin_login_required
def home_settings():
    from extensions import db
    from models import Categories
    import os
    from werkzeug.utils import secure_filename
    
    if request.method == 'POST':
        try:
            config_data = load_config()
            
            # Handle Banners
            home_banners = []
            banner_count = int(request.form.get('banner_count', 3))
            for i in range(banner_count):
                title = request.form.get(f'banner_title_{i}', '')
                subtitle = request.form.get(f'banner_subtitle_{i}', '')
                link_url = request.form.get(f'banner_link_{i}', '')
                current_path = request.form.get(f'banner_current_path_{i}', '')
                is_video = request.form.get(f'banner_current_is_video_{i}', '0') == '1'
                
                media_path = current_path
                media_file = request.files.get(f'banner_media_{i}')
                if media_file and media_file.filename != '' and allowed_file(media_file.filename):
                    filename = secure_filename(media_file.filename or '')
                    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
                    new_is_video = ext in ['mp4', 'webm']
                    
                    save_dir = os.path.join(current_app.root_path, 'static', 'img')
                    os.makedirs(save_dir, exist_ok=True)
                    save_path = os.path.join(save_dir, filename)
                    media_file.save(save_path)
                    
                    media_path = f'img/{filename}'
                    is_video = new_is_video
                
                home_banners.append({
                    'media_path': media_path,
                    'is_video': is_video,
                    'title': title,
                    'subtitle': subtitle,
                    'link_url': link_url
                })
            config_data['HOME_BANNERS'] = home_banners
            
            # Handle Categories
            featured_cats = []
            category_count = int(request.form.get('category_count', 4))
            for i in range(category_count):
                cat = request.form.get(f'category_{i}')
                if cat:
                    current_image = request.form.get(f'category_current_image_{i}', f'img/cat-{i+1}.jpg')
                    cat_image = current_image
                    
                    img_file = request.files.get(f'category_image_{i}')
                    if img_file and img_file.filename != '' and allowed_file(img_file.filename):
                        filename = secure_filename(img_file.filename or '')
                        save_dir = os.path.join(current_app.root_path, 'static', 'img')
                        os.makedirs(save_dir, exist_ok=True)
                        save_path = os.path.join(save_dir, filename)
                        img_file.save(save_path)
                        cat_image = f'img/{filename}'

                    featured_cats.append({
                        'slug': cat,
                        'image': cat_image
                    })
            config_data['HOME_CATEGORIES'] = featured_cats
            
            # Handle Offers
            home_offers = []
            offer_count = int(request.form.get('offer_count', 2))
            for i in range(offer_count):
                align = request.form.get(f'offer_align_{i}', 'center')
                subtitle = request.form.get(f'offer_subtitle_{i}', '')
                title = request.form.get(f'offer_title_{i}', '')
                text = request.form.get(f'offer_text_{i}', '')
                link_url = request.form.get(f'offer_link_{i}', '')
                current_bg = request.form.get(f'offer_current_bg_{i}', '')
                
                bg_image = current_bg
                bg_file = request.files.get(f'offer_bg_{i}')
                if bg_file and bg_file.filename != '' and allowed_file(bg_file.filename):
                    filename = secure_filename(bg_file.filename or '')
                    save_dir = os.path.join(current_app.root_path, 'static', 'img')
                    os.makedirs(save_dir, exist_ok=True)
                    save_path = os.path.join(save_dir, filename)
                    bg_file.save(save_path)
                    bg_image = f'img/{filename}'
                    
                home_offers.append({
                    'title': title,
                    'subtitle': subtitle,
                    'text': text,
                    'link_url': link_url,
                    'bg_image': bg_image,
                    'align': align
                })
            config_data['HOME_OFFERS'] = home_offers
            
            if save_config(config_data):
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({'success': True, 'message': 'Home settings updated successfully.'})
                flash('Home settings updated successfully.', 'success')
            else:
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({'success': False, 'message': 'Failed to save settings.'})
                flash('Failed to save settings.', 'error')
        except Exception as e:
            print(f"Error saving home settings: {e}")
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': str(e)})
            flash(f'Error updating settings: {str(e)}', 'error')
            
        return redirect(url_for('admin_bp.home_settings'))
        
    config = load_config()
    all_categories = db.session.scalars(db.select(Categories).filter_by(is_active=1).order_by(Categories.name)).all()
    return render_template('admin/home_settings.html', config=config, all_categories=all_categories)

@admin_bp.route('/api/category-sku-sequence')
@admin_login_required
def category_sku_sequence():
    category = request.args.get('category')
    if not category:
        return jsonify({'sequence': 1})
    
    try:
        from extensions import db
        from models import Products
        import re
        
        products = db.session.scalars(db.select(Products.sku).filter(Products.category == category, Products.sku != None, Products.sku != '')).all()
        
        max_seq = 0
        for sku in products:
            match = re.match(r'^[a-zA-Z]+-(\d+)', sku)
            if match:
                try:
                    seq_num = int(match.group(1))
                    if seq_num > max_seq:
                        max_seq = seq_num
                except ValueError:
                    continue
        
        return jsonify({'sequence': max_seq + 1})
    except Exception as e:
        return jsonify({'sequence': 1, 'error': str(e)})
