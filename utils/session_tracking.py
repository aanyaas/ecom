from flask import request, session
from user_agents import parse
from extensions import db
from models import UserSessions
from utils.session_helpers import get_or_create_guest_session
from sqlalchemy import text
from admin.admin_app import get_geolocation # Re-using geolocation helper

def track_user_session():
    """Middleware to track user sessions in the user_sessions table."""
    try:
        # Get or create a unique session ID
        session_id = session.get('guest_id')
        if not session_id:
            session_id = get_or_create_guest_session()
            
        if not session_id:
            import uuid
            session_id = str(uuid.uuid4())
            session['guest_id'] = session_id
        
        # User ID tracking (admin or normal user)
        # Assuming admin users use 'admin_id' and frontend users use 'user_id'
        user_id = session.get('admin_id') or session.get('user_id')
        
        ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ip_address and ',' in ip_address:
            ip_address = ip_address.split(',')[0].strip()
            
        user_agent_string = request.headers.get('User-Agent', '')
        user_agent = parse(user_agent_string)
        
        # Derive device type
        if user_agent.is_bot:
            device_type = 'Bot'
        elif user_agent.is_mobile:
            device_type = 'Mobile'
        elif user_agent.is_tablet:
            device_type = 'Tablet'
        elif user_agent.is_pc:
            device_type = 'Desktop'
        else:
            device_type = 'Unknown'
            
        browser = user_agent.browser.family
        browser_version = user_agent.browser.version_string
        platform = user_agent.os.family
        
        login_successful = 1 if user_id else 0
        
        # Fetch existing session to see if we need to insert or update
        existing_session = db.session.execute(
            db.select(UserSessions).filter_by(session_id=session_id).limit(1)
        ).scalar_one_or_none()

        if existing_session:
            # Update existing session
            existing_session.user_id = user_id
            existing_session.ip_address = ip_address
            existing_session.user_agent = user_agent_string
            existing_session.device_type = device_type
            existing_session.browser = browser
            existing_session.browser_version = browser_version
            existing_session.platform = platform
            existing_session.os_version = user_agent.os.version_string
            existing_session.login_successful = login_successful
            existing_session.last_activity = db.func.now()
            
            # If geolocation is missing and we have an IP, we could optionally update it here, 
            # but usually it's handled by a background task to avoid slowing down requests.
            # We'll let the background task handle country/city updates.
        else:
            # New session
            # Fetch geolocation quickly (with timeout)
            geo = get_geolocation(ip_address)
            
            new_session = UserSessions(
                session_id=session_id,
                user_id=user_id,
                ip_address=ip_address,
                user_agent=user_agent_string,
                device_type=device_type,
                browser=browser,
                browser_version=browser_version,
                platform=platform,
                os_version=user_agent.os.version_string,
                country=geo.get('country', 'Unknown'),
                city=geo.get('city', 'Unknown'),
                region=geo.get('region', 'Unknown'),
                login_successful=login_successful
            )
            db.session.add(new_session)

        db.session.commit()
    except Exception as e:
        print(f"Session tracking error: {e}")
        try:
            db.session.rollback()
        except:
            pass
