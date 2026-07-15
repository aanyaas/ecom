from typing import Optional
import datetime
import decimal
import enum

from sqlalchemy import BigInteger, DECIMAL, Date, DateTime, Double, Enum, ForeignKeyConstraint, Index, Integer, JSON, LargeBinary, String, TIMESTAMP, Text, text
from sqlalchemy.dialects.mysql import ENUM, TEXT, TINYINT, VARCHAR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass


class CouponsDiscountType(str, enum.Enum):
    PERCENTAGE = 'percentage'
    FIXED = 'fixed'


class LoyaltyLedgerTransactionType(str, enum.Enum):
    EARNED = 'earned'
    REDEEMED = 'redeemed'
    REFUNDED = 'refunded'
    EXPIRED = 'expired'
    REFERRAL_BONUS = 'referral_bonus'
    SIGNUP_BONUS = 'signup_bonus'


class OrderReturnsStatus(str, enum.Enum):
    REQUESTED = 'requested'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    PROCESSING = 'processing'
    COMPLETED = 'completed'


class AdminUsers(Base):
    __tablename__ = 'admin_users'
    __table_args__ = (
        Index('email', 'email', unique=True),
        Index('username', 'username', unique=True)
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(VARCHAR(50, charset='latin1', collation='latin1_swedish_ci'), nullable=False)
    password: Mapped[str] = mapped_column(VARCHAR(255, charset='latin1', collation='latin1_swedish_ci'), nullable=False)
    email: Mapped[str] = mapped_column(VARCHAR(100, charset='latin1', collation='latin1_swedish_ci'), nullable=False)
    role: Mapped[str] = mapped_column(VARCHAR(50, charset='latin1', collation='latin1_swedish_ci'), nullable=False, server_default=text("'editor'"))
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    is_active: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'1'"))
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    last_login: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    mobile_number: Mapped[Optional[str]] = mapped_column(VARCHAR(15, charset='latin1', collation='latin1_swedish_ci'))
    expiry_date: Mapped[Optional[datetime.date]] = mapped_column(Date)
    last_password_change: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    inventory_logs: Mapped[list['InventoryLogs']] = relationship('InventoryLogs', back_populates='admin')


class AdminRoles(Base):
    __tablename__ = 'admin_roles'
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(VARCHAR(50), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(VARCHAR(255))
    is_active: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'1'"))
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))


class AdminMenus(Base):
    __tablename__ = 'admin_menus'
    __table_args__ = (
        ForeignKeyConstraint(['parent_id'], ['admin_menus.id'], ondelete='SET NULL'),
    )
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(VARCHAR(100), nullable=False)
    endpoint: Mapped[Optional[str]] = mapped_column(VARCHAR(255))
    icon: Mapped[Optional[str]] = mapped_column(VARCHAR(100))
    parent_id: Mapped[Optional[int]] = mapped_column(Integer)
    sort_order: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("'0'"))
    is_active: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'1'"))
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    parent: Mapped[Optional['AdminMenus']] = relationship('AdminMenus', remote_side=[id])


class AdminRoleMenus(Base):
    __tablename__ = 'admin_role_menus'
    __table_args__ = (
        ForeignKeyConstraint(['role_id'], ['admin_roles.id'], ondelete='CASCADE'),
        ForeignKeyConstraint(['menu_id'], ['admin_menus.id'], ondelete='CASCADE'),
    )

    role_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    menu_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    can_view: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'1'"))
    can_add: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'0'"))
    can_edit: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'0'"))
    can_delete: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'0'"))

    role: Mapped['AdminRoles'] = relationship('AdminRoles')
    menu: Mapped['AdminMenus'] = relationship('AdminMenus')

class Categories(Base):
    __tablename__ = 'categories'
    __table_args__ = (
        ForeignKeyConstraint(['parent_id'], ['categories.id'], ondelete='SET NULL', onupdate='RESTRICT', name='categories_ibfk_1'),
        Index('parent_id', 'parent_id'),
        Index('slug', 'slug', unique=True)
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(VARCHAR(100, charset='utf8mb4', collation='utf8mb4_0900_ai_ci'), nullable=False)
    slug: Mapped[str] = mapped_column(VARCHAR(100, charset='utf8mb4', collation='utf8mb4_0900_ai_ci'), nullable=False)
    parent_id: Mapped[Optional[int]] = mapped_column(Integer)
    sort_order: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("'0'"))
    is_active: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'1'"))

    parent: Mapped[Optional['Categories']] = relationship('Categories', remote_side=[id], back_populates='parent_reverse')
    parent_reverse: Mapped[list['Categories']] = relationship('Categories', remote_side=[parent_id], back_populates='parent')


class CompanyInfo(Base):
    __tablename__ = 'company_info'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_name: Mapped[str] = mapped_column(VARCHAR(100, charset='latin1', collation='latin1_swedish_ci'), nullable=False)
    address: Mapped[str] = mapped_column(TEXT(charset='latin1', collation='latin1_swedish_ci'), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    updated_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text("'0000-00-00 00:00:00'"))
    phone: Mapped[Optional[str]] = mapped_column(VARCHAR(20, charset='latin1', collation='latin1_swedish_ci'))
    email: Mapped[Optional[str]] = mapped_column(VARCHAR(100, charset='latin1', collation='latin1_swedish_ci'))
    gstin: Mapped[Optional[str]] = mapped_column(VARCHAR(15, charset='latin1', collation='latin1_swedish_ci'))
    state_code: Mapped[Optional[str]] = mapped_column(VARCHAR(2, charset='latin1', collation='latin1_swedish_ci'))
    logo: Mapped[Optional[str]] = mapped_column(VARCHAR(255, charset='latin1', collation='latin1_swedish_ci'))
    pan: Mapped[Optional[str]] = mapped_column(String(10))
    state: Mapped[Optional[str]] = mapped_column(String(100))
    city: Mapped[Optional[str]] = mapped_column(String(100))
    pincode: Mapped[Optional[str]] = mapped_column(String(6))
    price_includes_gst: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'1'"))
    website: Mapped[Optional[str]] = mapped_column(VARCHAR(255, charset='latin1', collation='latin1_swedish_ci'))


class Coupons(Base):
    __tablename__ = 'coupons'
    __table_args__ = (
        Index('code', 'code', unique=True),
        Index('idx_coupons_code', 'code')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    discount_type: Mapped[CouponsDiscountType] = mapped_column(Enum(CouponsDiscountType, values_callable=lambda cls: [member.value for member in cls]), nullable=False)
    discount_value: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    expiry: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False)
    min_order: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    is_active: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'1'"))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'))


class CustomerTestimonials(Base):
    __tablename__ = 'customer_testimonials'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_name: Mapped[str] = mapped_column(String(80), nullable=False)
    rating: Mapped[int] = mapped_column(TINYINT, nullable=False, server_default=text("'5'"))
    feedback: Mapped[str] = mapped_column(Text, nullable=False)
    is_approved: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("'0'"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    email: Mapped[Optional[str]] = mapped_column(String(120))
    city: Mapped[Optional[str]] = mapped_column(String(60))
    customer_photo: Mapped[Optional[str]] = mapped_column(String(255))


class GiftCards(Base):
    __tablename__ = 'gift_cards'
    __table_args__ = (
        Index('code', 'code', unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    initial_balance: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    current_balance: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    expiry_date: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    is_active: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'1'"))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'))

    orders: Mapped[list['Orders']] = relationship('Orders', back_populates='gift_card')
    gift_card_transactions: Mapped[list['GiftCardTransactions']] = relationship('GiftCardTransactions', back_populates='gift_card')


class OtpVerifications(Base):
    __tablename__ = 'otp_verifications'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    otp: Mapped[str] = mapped_column(String(10), nullable=False)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'))


class PincodeStateCity(Base):
    __tablename__ = 'pincode_state_city'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    city: Mapped[Optional[str]] = mapped_column(VARCHAR(255, charset='latin1', collation='latin1_swedish_ci'))
    division_name: Mapped[Optional[str]] = mapped_column(VARCHAR(255, charset='latin1', collation='latin1_swedish_ci'))
    office_name: Mapped[Optional[str]] = mapped_column(VARCHAR(255, charset='latin1', collation='latin1_swedish_ci'))
    pincode: Mapped[Optional[str]] = mapped_column(VARCHAR(255, charset='latin1', collation='latin1_swedish_ci'))
    state_name: Mapped[Optional[str]] = mapped_column(VARCHAR(255, charset='latin1', collation='latin1_swedish_ci'))
    state_code: Mapped[Optional[str]] = mapped_column(VARCHAR(2, charset='latin1', collation='latin1_swedish_ci'))


class PosOrders(Base):
    __tablename__ = 'pos_orders'
    __table_args__ = (
        Index('user_id', 'user_name'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_date: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    total_amount: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    payment_method: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'completed'"))
    subtotal: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    order_dateonly: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    user_name: Mapped[Optional[str]] = mapped_column(String(100))
    invoice_number: Mapped[Optional[str]] = mapped_column(String(50))
    invoice_date: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    split_payments: Mapped[Optional[str]] = mapped_column(Text)
    customer_name: Mapped[Optional[str]] = mapped_column(String(100))
    customer_mobile: Mapped[Optional[str]] = mapped_column(String(20))
    customer_email: Mapped[Optional[str]] = mapped_column(String(100))
    discount_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    taxable_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    cgst_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    sgst_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    igst_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    total_gst: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    sales_channel: Mapped[Optional[str]] = mapped_column(String(50), server_default=text("'POS'"))

    pos_order_items: Mapped[list['PosOrderItems']] = relationship('PosOrderItems', back_populates='order')


class Products(Base):
    __tablename__ = 'products'
    __table_args__ = (
        Index('ft_search', 'name', 'category', 'material', 'color', 'description', mysql_prefix='FULLTEXT'),
        Index('idx_products_category', 'category'),
        Index('idx_products_is_active', 'is_active'),
        Index('idx_products_price', 'price'),
        Index('uq_sku', 'sku', unique=True)
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(VARCHAR(100, charset='latin1', collation='latin1_swedish_ci'), nullable=False)
    price: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    image: Mapped[str] = mapped_column(VARCHAR(255, charset='latin1', collation='latin1_swedish_ci'), nullable=False, server_default=text("''"))
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    description: Mapped[Optional[str]] = mapped_column(TEXT(charset='latin1', collation='latin1_swedish_ci'))
    old_price: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2))
    category: Mapped[Optional[str]] = mapped_column(VARCHAR(50, charset='latin1', collation='latin1_swedish_ci'))
    material: Mapped[Optional[str]] = mapped_column(VARCHAR(50, charset='latin1', collation='latin1_swedish_ci'))
    views: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("'0'"))
    sku: Mapped[Optional[str]] = mapped_column(VARCHAR(50, charset='latin1', collation='latin1_swedish_ci'))
    sku_variant: Mapped[Optional[str]] = mapped_column(String(100))
    created_by_supplier: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'0'"))
    is_active: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'1'"))
    reorder_level: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("'10'"))
    stock_quantity: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("'0'"))
    needs_restock: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'0'"))
    hsn_code: Mapped[Optional[str]] = mapped_column(VARCHAR(10, charset='latin1', collation='latin1_swedish_ci'))
    mrp: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2))
    size: Mapped[Optional[str]] = mapped_column(VARCHAR(20, charset='latin1', collation='latin1_swedish_ci'))
    color: Mapped[Optional[str]] = mapped_column(VARCHAR(30, charset='latin1', collation='latin1_swedish_ci'))
    item_height: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2))
    item_width: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2))
    item_length: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2))
    item_weight: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2))
    material_cost: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2))
    gst_rate: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(5, 2), server_default=text("'18.00'"))
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    product_features: Mapped[Optional[str]] = mapped_column(TEXT(charset='latin1', collation='latin1_swedish_ci'))
    care_instructions: Mapped[Optional[str]] = mapped_column(TEXT(charset='latin1', collation='latin1_swedish_ci'))
    meta_title: Mapped[Optional[str]] = mapped_column(VARCHAR(255, charset='latin1', collation='latin1_swedish_ci'))
    meta_keywords: Mapped[Optional[str]] = mapped_column(VARCHAR(255, charset='latin1', collation='latin1_swedish_ci'))
    meta_description: Mapped[Optional[str]] = mapped_column(TEXT(charset='latin1', collation='latin1_swedish_ci'))

    cart: Mapped[list['Cart']] = relationship('Cart', back_populates='product')
    guest_cart: Mapped[list['GuestCart']] = relationship('GuestCart', back_populates='product')
    inventory_logs: Mapped[list['InventoryLogs']] = relationship('InventoryLogs', back_populates='product')
    pos_order_items: Mapped[list['PosOrderItems']] = relationship('PosOrderItems', back_populates='product')
    product_images: Mapped[list['ProductImages']] = relationship('ProductImages', back_populates='product')
    order_items: Mapped[list['OrderItems']] = relationship('OrderItems', back_populates='product')
    product_reviews: Mapped[list['ProductReviews']] = relationship('ProductReviews', back_populates='product')
    wishlist_items: Mapped[list['WishlistItems']] = relationship('WishlistItems', back_populates='product')


class ReturnItems(Base):
    __tablename__ = 'return_items'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    return_id: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    refund_taxable_value: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    refund_cgst: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    refund_sgst: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    refund_igst: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    refund_total: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))


class Subscribers(Base):
    __tablename__ = 'subscribers'
    __table_args__ = (
        Index('email', 'email', unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(VARCHAR(255, charset='utf8mb3', collation='utf8mb3_general_ci'), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    user_id: Mapped[Optional[int]] = mapped_column(Integer)


class Users(Base):
    __tablename__ = 'users'
    __table_args__ = (
        Index('referral_code', 'referral_code', unique=True),
        Index('uq_email', 'email', unique=True),
        Index('username', 'username', unique=True)
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(VARCHAR(50, charset='utf8mb3', collation='utf8mb3_general_ci'), nullable=False)
    password: Mapped[str] = mapped_column(VARCHAR(255, charset='utf8mb3', collation='utf8mb3_general_ci'), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    first_name: Mapped[Optional[str]] = mapped_column(VARCHAR(255, charset='utf8mb3', collation='utf8mb3_general_ci'))
    last_name: Mapped[Optional[str]] = mapped_column(VARCHAR(255, charset='utf8mb3', collation='utf8mb3_general_ci'))
    email: Mapped[Optional[str]] = mapped_column(VARCHAR(255, charset='utf8mb3', collation='utf8mb3_general_ci'))
    is_active: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'1'"))
    mobile_number: Mapped[Optional[str]] = mapped_column(VARCHAR(15, charset='utf8mb3', collation='utf8mb3_general_ci'))
    date_of_birth: Mapped[Optional[datetime.date]] = mapped_column(Date)
    marriage_anniversary: Mapped[Optional[datetime.date]] = mapped_column(Date)
    expiry_date: Mapped[Optional[datetime.date]] = mapped_column(Date)
    updated_at: Mapped[Optional[datetime.date]] = mapped_column(Date)
    gstin: Mapped[Optional[str]] = mapped_column(VARCHAR(15, charset='utf8mb3', collation='utf8mb3_general_ci'))
    profile_picture: Mapped[Optional[str]] = mapped_column(String(255))
    gender: Mapped[Optional[str]] = mapped_column(String(10))
    alternate_mobile: Mapped[Optional[str]] = mapped_column(String(15))
    referral_code: Mapped[Optional[str]] = mapped_column(String(50))
    referred_by: Mapped[Optional[int]] = mapped_column(Integer)

    cart: Mapped[list['Cart']] = relationship('Cart', back_populates='user')
    orders: Mapped[list['Orders']] = relationship('Orders', back_populates='user')
    password_reset_tokens: Mapped[list['PasswordResetTokens']] = relationship('PasswordResetTokens', back_populates='user')
    user_addresses: Mapped[list['UserAddresses']] = relationship('UserAddresses', back_populates='user')
    wishlists: Mapped[list['Wishlists']] = relationship('Wishlists', back_populates='user')
    loyalty_ledger: Mapped[list['LoyaltyLedger']] = relationship('LoyaltyLedger', back_populates='user')
    order_returns: Mapped[list['OrderReturns']] = relationship('OrderReturns', back_populates='user')
    product_reviews: Mapped[list['ProductReviews']] = relationship('ProductReviews', back_populates='user')


class Cart(Base):
    __tablename__ = 'cart'
    __table_args__ = (
        ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='RESTRICT', onupdate='RESTRICT', name='cart_ibfk_2'),
        ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT', onupdate='RESTRICT', name='cart_ibfk_1'),
        Index('product_id', 'product_id'),
        Index('user_id', 'user_id', 'product_id', unique=True)
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    quantity: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("'1'"))
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    product: Mapped['Products'] = relationship('Products', back_populates='cart')
    user: Mapped['Users'] = relationship('Users', back_populates='cart')


class GuestCart(Base):
    __tablename__ = 'guest_cart'
    __table_args__ = (
        ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='RESTRICT', onupdate='RESTRICT', name='guest_cart_ibfk_1'),
        Index('guest_id', 'guest_id'),
        Index('product_id', 'product_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guest_id: Mapped[str] = mapped_column(VARCHAR(36, charset='latin1', collation='latin1_swedish_ci'), nullable=False)
    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("'1'"))
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    product: Mapped['Products'] = relationship('Products', back_populates='guest_cart')


class InventoryLogs(Base):
    __tablename__ = 'inventory_logs'
    __table_args__ = (
        ForeignKeyConstraint(['admin_id'], ['admin_users.id'], ondelete='SET NULL', onupdate='RESTRICT', name='inventory_logs_ibfk_2'),
        ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE', onupdate='RESTRICT', name='inventory_logs_ibfk_1'),
        Index('admin_id', 'admin_id'),
        Index('idx_inventory_logs_date', 'created_at'),
        Index('idx_inventory_logs_product', 'product_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    new_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    adjustment_type: Mapped[str] = mapped_column(VARCHAR(20, charset='latin1', collation='latin1_swedish_ci'), nullable=False, server_default=text("'manual'"))
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    adjustment: Mapped[int] = mapped_column(Integer, nullable=False)
    admin_id: Mapped[Optional[int]] = mapped_column(Integer)
    notes: Mapped[Optional[str]] = mapped_column(TEXT(charset='latin1', collation='latin1_swedish_ci'))
    adjusted_by: Mapped[Optional[str]] = mapped_column(VARCHAR(100, charset='latin1', collation='latin1_swedish_ci'))
    reference_id: Mapped[Optional[int]] = mapped_column(Integer)

    admin: Mapped[Optional['AdminUsers']] = relationship('AdminUsers', back_populates='inventory_logs')
    product: Mapped['Products'] = relationship('Products', back_populates='inventory_logs')


class Orders(Base):
    __tablename__ = 'orders'
    __table_args__ = (
        ForeignKeyConstraint(['gift_card_id'], ['gift_cards.id'], name='orders_ibfk_2'),
        ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT', onupdate='RESTRICT', name='orders_ibfk_1'),
        Index('gift_card_id', 'gift_card_id'),
        Index('idx_orders_status', 'status'),
        Index('idx_orders_user_id', 'user_id'),
        Index('user_id', 'user_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    order_date: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    total_amount: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    payment_method: Mapped[str] = mapped_column(VARCHAR(50, charset='utf8mb3', collation='utf8mb3_general_ci'), nullable=False)
    billing_address: Mapped[str] = mapped_column(TEXT(charset='utf8mb3', collation='utf8mb3_general_ci'), nullable=False)
    shipping_address: Mapped[str] = mapped_column(TEXT(charset='utf8mb3', collation='utf8mb3_general_ci'), nullable=False)
    status: Mapped[str] = mapped_column(VARCHAR(20, charset='utf8mb3', collation='utf8mb3_general_ci'), nullable=False)
    subtotal: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    order_dateonly: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    invoice_number: Mapped[Optional[str]] = mapped_column(VARCHAR(50, charset='utf8mb3', collation='utf8mb3_general_ci'))
    invoice_date: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    shipping_charge: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    discount_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    coupon_code: Mapped[Optional[str]] = mapped_column(VARCHAR(50, charset='utf8mb3', collation='utf8mb3_general_ci'))
    accepted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    rtd_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    shipped_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    courier_name: Mapped[Optional[str]] = mapped_column(VARCHAR(100, charset='utf8mb3', collation='utf8mb3_general_ci'))
    tracking_id: Mapped[Optional[str]] = mapped_column(VARCHAR(100, charset='utf8mb3', collation='utf8mb3_general_ci'))
    delivered_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    cancelled_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    cancellation_reason: Mapped[Optional[str]] = mapped_column(TEXT(charset='utf8mb3', collation='utf8mb3_general_ci'))
    returned_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    return_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2))
    return_reason: Mapped[Optional[str]] = mapped_column(TEXT(charset='utf8mb3', collation='utf8mb3_general_ci'))
    payment_status: Mapped[Optional[str]] = mapped_column(String(20), server_default=text("'unpaid'"))
    refund_status: Mapped[Optional[str]] = mapped_column(String(20))
    merchant_refund_id: Mapped[Optional[str]] = mapped_column(String(100))
    taxable_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    cgst_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    sgst_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    igst_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    total_gst: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    sales_channel: Mapped[Optional[str]] = mapped_column(String(50), server_default=text("'Native'"))
    loyalty_points_used: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("'0'"))
    gift_card_id: Mapped[Optional[int]] = mapped_column(Integer)
    gift_card_discount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    external_order_id: Mapped[Optional[str]] = mapped_column(String(100))

    gift_card: Mapped[Optional['GiftCards']] = relationship('GiftCards', back_populates='orders')
    user: Mapped['Users'] = relationship('Users', back_populates='orders')
    gift_card_transactions: Mapped[list['GiftCardTransactions']] = relationship('GiftCardTransactions', back_populates='order')
    loyalty_ledger: Mapped[list['LoyaltyLedger']] = relationship('LoyaltyLedger', back_populates='order')
    order_items: Mapped[list['OrderItems']] = relationship('OrderItems', back_populates='order')
    order_returns: Mapped[list['OrderReturns']] = relationship('OrderReturns', back_populates='order')
    product_reviews: Mapped[list['ProductReviews']] = relationship('ProductReviews', back_populates='order')


class PasswordResetTokens(Base):
    __tablename__ = 'password_reset_tokens'
    __table_args__ = (
        ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE', onupdate='RESTRICT', name='password_reset_tokens_ibfk_1'),
        Index('user_id', 'user_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    token: Mapped[str] = mapped_column(VARCHAR(255, charset='latin1', collation='latin1_swedish_ci'), nullable=False)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    used: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("'0'"))
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    user: Mapped['Users'] = relationship('Users', back_populates='password_reset_tokens')


class PosOrderItems(Base):
    __tablename__ = 'pos_order_items'
    __table_args__ = (
        ForeignKeyConstraint(['order_id'], ['pos_orders.id'], ondelete='CASCADE', name='fk_pos_order_id'),
        ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='RESTRICT', name='fk_pos_product_id'),
        Index('order_id', 'order_id'),
        Index('product_id', 'product_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    product_name: Mapped[Optional[str]] = mapped_column(String(255))
    gst_rate: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(5, 2), server_default=text("'0.00'"))
    cgst_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    sgst_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    igst_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    taxable_value: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    hsn_code: Mapped[Optional[str]] = mapped_column(String(50))

    order: Mapped['PosOrders'] = relationship('PosOrders', back_populates='pos_order_items')
    product: Mapped['Products'] = relationship('Products', back_populates='pos_order_items')


class ProductImages(Base):
    __tablename__ = 'product_images'
    __table_args__ = (
        ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE', onupdate='RESTRICT', name='product_images_ibfk_1'),
        Index('idx_product_id', 'product_id'),
        Index('unique_product_type', 'product_id', 'image_type', unique=True)
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    image_filename: Mapped[str] = mapped_column(VARCHAR(255, charset='utf8mb3', collation='utf8mb3_general_ci'), nullable=False)
    image_type: Mapped[str] = mapped_column(VARCHAR(50, charset='utf8mb3', collation='utf8mb3_general_ci'), nullable=False)
    sort_order: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("'0'"))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'))
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP)

    product: Mapped['Products'] = relationship('Products', back_populates='product_images')


class UserAddresses(Base):
    __tablename__ = 'user_addresses'
    __table_args__ = (
        ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE', onupdate='RESTRICT', name='user_addresses_ibfk_1'),
        Index('user_id', 'user_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    address_type: Mapped[str] = mapped_column(VARCHAR(20, charset='utf8mb3', collation='utf8mb3_general_ci'), nullable=False)
    full_name: Mapped[str] = mapped_column(VARCHAR(100, charset='utf8mb3', collation='utf8mb3_general_ci'), nullable=False)
    mobile_number: Mapped[str] = mapped_column(VARCHAR(15, charset='utf8mb3', collation='utf8mb3_general_ci'), nullable=False)
    address_line1: Mapped[str] = mapped_column(VARCHAR(255, charset='utf8mb3', collation='utf8mb3_general_ci'), nullable=False)
    city: Mapped[str] = mapped_column(VARCHAR(100, charset='utf8mb3', collation='utf8mb3_general_ci'), nullable=False)
    state: Mapped[str] = mapped_column(VARCHAR(100, charset='utf8mb3', collation='utf8mb3_general_ci'), nullable=False)
    country: Mapped[str] = mapped_column(VARCHAR(100, charset='utf8mb3', collation='utf8mb3_general_ci'), nullable=False, server_default=text("'India'"))
    postal_code: Mapped[str] = mapped_column(VARCHAR(20, charset='utf8mb3', collation='utf8mb3_general_ci'), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    address_line2: Mapped[Optional[str]] = mapped_column(VARCHAR(255, charset='utf8mb3', collation='utf8mb3_general_ci'))
    state_code: Mapped[Optional[str]] = mapped_column(VARCHAR(10, charset='utf8mb3', collation='utf8mb3_general_ci'), server_default=text("''"))
    email: Mapped[Optional[str]] = mapped_column(VARCHAR(255, charset='utf8mb3', collation='utf8mb3_general_ci'))
    is_default: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'0'"))
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP)
    gst_number: Mapped[Optional[str]] = mapped_column(String(50))
    company_name: Mapped[Optional[str]] = mapped_column(String(255))

    user: Mapped['Users'] = relationship('Users', back_populates='user_addresses')


class Wishlists(Base):
    __tablename__ = 'wishlists'
    __table_args__ = (
        ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE', name='wishlists_ibfk_1'),
        Index('share_token', 'share_token', unique=True),
        Index('user_id', 'user_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    share_token: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(Integer)
    session_id: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'))

    user: Mapped[Optional['Users']] = relationship('Users', back_populates='wishlists')
    wishlist_items: Mapped[list['WishlistItems']] = relationship('WishlistItems', back_populates='wishlist')


class GiftCardTransactions(Base):
    __tablename__ = 'gift_card_transactions'
    __table_args__ = (
        ForeignKeyConstraint(['gift_card_id'], ['gift_cards.id'], name='gift_card_transactions_ibfk_1'),
        ForeignKeyConstraint(['order_id'], ['orders.id'], name='gift_card_transactions_ibfk_2'),
        Index('gift_card_id', 'gift_card_id'),
        Index('order_id', 'order_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gift_card_id: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_used: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    order_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'))

    gift_card: Mapped['GiftCards'] = relationship('GiftCards', back_populates='gift_card_transactions')
    order: Mapped[Optional['Orders']] = relationship('Orders', back_populates='gift_card_transactions')


class LoyaltyLedger(Base):
    __tablename__ = 'loyalty_ledger'
    __table_args__ = (
        ForeignKeyConstraint(['order_id'], ['orders.id'], name='loyalty_ledger_ibfk_2'),
        ForeignKeyConstraint(['user_id'], ['users.id'], name='loyalty_ledger_ibfk_1'),
        Index('order_id', 'order_id'),
        Index('user_id', 'user_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    transaction_type: Mapped[LoyaltyLedgerTransactionType] = mapped_column(Enum(LoyaltyLedgerTransactionType, values_callable=lambda cls: [member.value for member in cls]), nullable=False)
    order_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'))

    order: Mapped[Optional['Orders']] = relationship('Orders', back_populates='loyalty_ledger')
    user: Mapped['Users'] = relationship('Users', back_populates='loyalty_ledger')


class OrderItems(Base):
    __tablename__ = 'order_items'
    __table_args__ = (
        ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='RESTRICT', onupdate='RESTRICT', name='order_items_ibfk_1'),
        ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='RESTRICT', onupdate='RESTRICT', name='order_items_ibfk_2'),
        Index('idx_order_items_order_id', 'order_id'),
        Index('idx_order_items_product_id', 'product_id'),
        Index('order_id', 'order_id'),
        Index('product_id', 'product_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    product_name: Mapped[Optional[str]] = mapped_column(VARCHAR(255, charset='utf8mb3', collation='utf8mb3_general_ci'))
    gst_rate: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(5, 2), server_default=text("'0.00'"))
    cgst_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    sgst_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    igst_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    taxable_value: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), server_default=text("'0.00'"))
    hsn_code: Mapped[Optional[str]] = mapped_column(String(50))

    order: Mapped['Orders'] = relationship('Orders', back_populates='order_items')
    product: Mapped['Products'] = relationship('Products', back_populates='order_items')


class OrderReturns(Base):
    __tablename__ = 'order_returns'
    __table_args__ = (
        ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='RESTRICT', onupdate='RESTRICT', name='order_returns_ibfk_1'),
        ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT', onupdate='RESTRICT', name='order_returns_ibfk_2'),
        Index('order_id', 'order_id'),
        Index('user_id', 'user_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(VARCHAR(255, charset='utf8mb3', collation='utf8mb3_general_ci'), nullable=False)
    remarks: Mapped[Optional[str]] = mapped_column(TEXT(charset='utf8mb3', collation='utf8mb3_general_ci'))
    evidence_files: Mapped[Optional[dict]] = mapped_column(JSON)
    status: Mapped[Optional[OrderReturnsStatus]] = mapped_column(Enum(OrderReturnsStatus, values_callable=lambda cls: [member.value for member in cls]), server_default=text("'requested'"))
    requested_date: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'))
    updated_date: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'))

    order: Mapped['Orders'] = relationship('Orders', back_populates='order_returns')
    user: Mapped['Users'] = relationship('Users', back_populates='order_returns')


class ProductReviews(Base):
    __tablename__ = 'product_reviews'
    __table_args__ = (
        ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='RESTRICT', onupdate='RESTRICT', name='product_reviews_ibfk_3'),
        ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='RESTRICT', onupdate='RESTRICT', name='product_reviews_ibfk_2'),
        ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT', onupdate='RESTRICT', name='product_reviews_ibfk_1'),
        Index('order_id', 'order_id'),
        Index('product_id', 'product_id'),
        Index('user_id', 'user_id', 'product_id', 'order_id', unique=True)
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    order_id: Mapped[int] = mapped_column(Integer, nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    title: Mapped[Optional[str]] = mapped_column(VARCHAR(255, charset='utf8mb3', collation='utf8mb3_general_ci'))
    review_text: Mapped[Optional[str]] = mapped_column(TEXT(charset='utf8mb3', collation='utf8mb3_general_ci'))
    media_files: Mapped[Optional[dict]] = mapped_column(JSON)

    order: Mapped['Orders'] = relationship('Orders', back_populates='product_reviews')
    product: Mapped['Products'] = relationship('Products', back_populates='product_reviews')
    user: Mapped['Users'] = relationship('Users', back_populates='product_reviews')


class WishlistItems(Base):
    __tablename__ = 'wishlist_items'
    __table_args__ = (
        ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE', name='wishlist_items_ibfk_2'),
        ForeignKeyConstraint(['wishlist_id'], ['wishlists.id'], ondelete='CASCADE', name='wishlist_items_ibfk_1'),
        Index('product_id', 'product_id'),
        Index('unique_wishlist_product', 'wishlist_id', 'product_id', unique=True)
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wishlist_id: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    added_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'))

    product: Mapped['Products'] = relationship('Products', back_populates='wishlist_items')
    wishlist: Mapped['Wishlists'] = relationship('Wishlists', back_populates='wishlist_items')

class UserSessions(Base):
    __tablename__ = 'user_sessions'

    session_id: Mapped[str] = mapped_column(String(255), primary_key=True, nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(Integer)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))
    country: Mapped[Optional[str]] = mapped_column(String(100), server_default=text("'Unknown'"))
    city: Mapped[Optional[str]] = mapped_column(String(100), server_default=text("'Unknown'"))
    region: Mapped[Optional[str]] = mapped_column(String(100))
    user_agent: Mapped[Optional[str]] = mapped_column(String(255))
    device_type: Mapped[Optional[str]] = mapped_column(String(50), server_default=text("'Unknown'"))
    platform: Mapped[Optional[str]] = mapped_column(String(50), server_default=text("'Unknown'"))
    browser: Mapped[Optional[str]] = mapped_column(String(50), server_default=text("'Unknown'"))
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'))
    last_activity: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP)
    login_successful: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'0'"))
    logout_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    browser_version: Mapped[Optional[str]] = mapped_column(String(50))
    os_version: Mapped[Optional[str]] = mapped_column(String(50))

