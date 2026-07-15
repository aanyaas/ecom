from extensions import db
from sqlalchemy import text

class ProductRepository:
    @staticmethod
    def get_by_id(product_id):
        """Fetch a single active product by ID, including its category details."""
        query = """
            SELECT p.*, c.name as category_name
            FROM products p
            LEFT JOIN categories c ON p.category = c.slug
            WHERE p.id = :id AND p.is_active = 1
        """
        result = db.session.execute(text(query), {'id': product_id}).mappings().fetchone()
        return dict(result) if result else None

    @staticmethod
    def get_images(product_id):
        """Fetch all associated gallery images for a given product."""
        query = """
            SELECT image_filename, image_type, sort_order
            FROM product_images
            WHERE product_id = :id
            ORDER BY sort_order ASC
        """
        results = db.session.execute(text(query), {'id': product_id}).mappings().fetchall()
        return [dict(row) for row in results]

    @staticmethod
    def get_group_variants(sku_variant):
        """Fetch all dynamic variants sharing the same Group SKU/variant code."""
        if not sku_variant:
            return []
        query = """
            SELECT id, color, size, image, stock_quantity, price
            FROM products
            WHERE sku_variant = :sku_variant AND is_active = 1
        """
        results = db.session.execute(text(query), {'sku_variant': sku_variant}).mappings().fetchall()
        return [dict(row) for row in results]

    @staticmethod
    def get_related(category_slug, exclude_id, limit=4):
        """Fetch curated related products in the same category, excluding current product."""
        query = """
            SELECT id, name, price as unit_price, image, stock_quantity
            FROM products
            WHERE category = :category_slug AND id != :exclude_id AND is_active = 1
            LIMIT :limit
        """
        results = db.session.execute(text(query), {
            'category_slug': category_slug,
            'exclude_id': exclude_id,
            'limit': limit
        }).mappings().fetchall()
        return [dict(row) for row in results]

