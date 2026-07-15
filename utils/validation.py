import re

def validate_email(email: str) -> bool:
    """Validate email using standard regular expression."""
    if not email:
        return False
    return re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) is not None

def validate_phone(phone) -> bool:
    """Validate phone number using standard regular expression."""
    if not phone:
        return False
    return bool(re.match(r'^[+]?[\d\s\-]{10,15}$', str(phone).strip()))
