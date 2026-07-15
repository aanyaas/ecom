from flask import render_template, request, redirect, url_for, flash, session, jsonify
from admin.admin_app import admin_bp, admin_login_required
from models import AdminRoles, AdminMenus, AdminRoleMenus
from extensions import db
from sqlalchemy import text

@admin_bp.route('/roles')
@admin_login_required
def roles_list():
    if session.get('admin_role') != 'admin':
        flash('Permission denied.', 'danger')
        return redirect(url_for('admin_bp.admin_dashboard'))
    
    roles = db.session.query(AdminRoles).all()
    return render_template('admin/roles_menus/roles.html', roles=roles)

@admin_bp.route('/roles/add', methods=['POST'])
@admin_login_required
def add_role():
    if session.get('admin_role') != 'admin':
        return jsonify({'success': False, 'message': 'Permission denied.'}), 403
        
    name = request.form.get('name', '').strip().lower()
    description = request.form.get('description', '').strip()
    
    if not name:
        return jsonify({'success': False, 'message': 'Role name is required.'}), 400
        
    existing = db.session.query(AdminRoles).filter_by(name=name).first()
    if existing:
        return jsonify({'success': False, 'message': 'Role already exists.'}), 400
        
    try:
        new_role = AdminRoles(name=name, description=description)
        db.session.add(new_role)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Role added successfully.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/roles/delete/<int:role_id>', methods=['POST'])
@admin_login_required
def delete_role(role_id):
    if session.get('admin_role') != 'admin':
        return jsonify({'success': False, 'message': 'Permission denied.'}), 403
        
    role = db.session.query(AdminRoles).get(role_id)
    if not role:
        return jsonify({'success': False, 'message': 'Role not found.'}), 404
        
    if role.name == 'admin':
        return jsonify({'success': False, 'message': 'Cannot delete the admin role.'}), 400
        
    try:
        db.session.delete(role)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Role deleted successfully.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/roles/toggle/<int:role_id>', methods=['POST'])
@admin_login_required
def toggle_role(role_id):
    if session.get('admin_role') != 'admin':
        return jsonify({'success': False, 'message': 'Permission denied.'}), 403
        
    role = db.session.query(AdminRoles).get(role_id)
    if not role:
        return jsonify({'success': False, 'message': 'Role not found.'}), 404
        
    if role.name == 'admin':
        return jsonify({'success': False, 'message': 'Cannot disable the admin role.'}), 400
        
    try:
        role.is_active = 1 if role.is_active == 0 else 0
        db.session.commit()
        return jsonify({'success': True, 'message': 'Role status updated.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/menus')
@admin_login_required
def menus_list():
    if session.get('admin_role') != 'admin':
        flash('Permission denied.', 'danger')
        return redirect(url_for('admin_bp.admin_dashboard'))
    
    menus = db.session.query(AdminMenus).order_by(AdminMenus.sort_order.asc()).all()
    return render_template('admin/roles_menus/menus.html', menus=menus)

@admin_bp.route('/menus/add', methods=['POST'])
@admin_login_required
def add_menu():
    if session.get('admin_role') != 'admin':
        return jsonify({'success': False, 'message': 'Permission denied.'}), 403
        
    name = request.form.get('name', '').strip()
    endpoint = request.form.get('endpoint', '').strip()
    icon = request.form.get('icon', '').strip()
    sort_order = request.form.get('sort_order', 0, type=int)
    
    if not name or not endpoint:
        return jsonify({'success': False, 'message': 'Name and endpoint are required.'}), 400
        
    try:
        new_menu = AdminMenus(name=name, endpoint=endpoint, icon=icon, sort_order=sort_order)
        db.session.add(new_menu)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Menu added successfully.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/menus/delete/<int:menu_id>', methods=['POST'])
@admin_login_required
def delete_menu(menu_id):
    if session.get('admin_role') != 'admin':
        return jsonify({'success': False, 'message': 'Permission denied.'}), 403
        
    menu = db.session.query(AdminMenus).get(menu_id)
    if not menu:
        return jsonify({'success': False, 'message': 'Menu not found.'}), 404
        
    try:
        db.session.delete(menu)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Menu deleted successfully.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/menus/edit/<int:menu_id>', methods=['GET', 'POST'])
@admin_login_required
def edit_menu(menu_id):
    if session.get('admin_role') != 'admin':
        return jsonify({'success': False, 'message': 'Permission denied.'}), 403

    menu = db.session.query(AdminMenus).get(menu_id)
    if not menu:
        return jsonify({'success': False, 'message': 'Menu not found.'}), 404

    if request.method == 'GET':
        return jsonify({
            'success': True,
            'id': menu.id,
            'name': menu.name,
            'endpoint': menu.endpoint,
            'icon': menu.icon or '',
            'sort_order': menu.sort_order
        })

    # POST: update
    name = request.form.get('name', '').strip()
    endpoint = request.form.get('endpoint', '').strip()
    icon = request.form.get('icon', '').strip()
    sort_order = request.form.get('sort_order', 0, type=int)

    if not name or not endpoint:
        return jsonify({'success': False, 'message': 'Name and endpoint are required.'}), 400

    try:
        menu.name = name
        menu.endpoint = endpoint
        menu.icon = icon
        menu.sort_order = sort_order
        db.session.commit()
        return jsonify({'success': True, 'message': 'Menu updated successfully.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/roles/<int:role_id>/assign', methods=['GET', 'POST'])
@admin_login_required
def assign_menus(role_id):
    if session.get('admin_role') != 'admin':
        flash('Permission denied.', 'danger')
        return redirect(url_for('admin_bp.admin_dashboard'))
        
    role = db.session.query(AdminRoles).get(role_id)
    if not role:
        flash('Role not found.', 'danger')
        return redirect(url_for('admin_bp.roles_list'))
        
    menus = db.session.query(AdminMenus).order_by(AdminMenus.sort_order.asc()).all()
    
    if request.method == 'POST':
        try:
            # Clear existing for this role
            db.session.query(AdminRoleMenus).filter_by(role_id=role_id).delete()
            
            # Form format: menu_{id}_view, menu_{id}_add, etc.
            assigned = {}
            for key, val in request.form.items():
                if key.startswith('menu_'):
                    parts = key.split('_')
                    if len(parts) == 3:
                        m_id = int(parts[1])
                        action = parts[2]
                        if m_id not in assigned:
                            assigned[m_id] = {'can_view': 0, 'can_add': 0, 'can_edit': 0, 'can_delete': 0}
                        assigned[m_id][f'can_{action}'] = 1
            
            for m_id, perms in assigned.items():
                rm = AdminRoleMenus(
                    role_id=role_id,
                    menu_id=m_id,
                    can_view=perms.get('can_view', 0),
                    can_add=perms.get('can_add', 0),
                    can_edit=perms.get('can_edit', 0),
                    can_delete=perms.get('can_delete', 0)
                )
                db.session.add(rm)
                
            db.session.commit()
            flash('Permissions updated successfully.', 'success')
            return redirect(url_for('admin_bp.assign_menus', role_id=role_id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating permissions: {str(e)}', 'danger')
            
    # GET Request: Fetch existing mappings
    mappings = db.session.query(AdminRoleMenus).filter_by(role_id=role_id).all()
    perms_map = {m.menu_id: m for m in mappings}
    
    return render_template('admin/roles_menus/assign.html', role=role, menus=menus, perms_map=perms_map)
