-- ==============================================================================
-- 02_seed_business_data.sql
-- Seed realistic business analytics data for Phase 1
-- Tables: customers, products, orders, order_items
-- ==============================================================================

-- 1. Create Tables in analytics Schema
CREATE TABLE IF NOT EXISTS analytics.customers (
    id SERIAL PRIMARY KEY,
    first_name VARCHAR(50) NOT NULL,
    last_name VARCHAR(50) NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    country VARCHAR(50) NOT NULL,
    city VARCHAR(50) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS analytics.products (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    category VARCHAR(50) NOT NULL,
    price NUMERIC(10, 2) NOT NULL,
    cost NUMERIC(10, 2) NOT NULL,
    stock_quantity INT NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS analytics.orders (
    id SERIAL PRIMARY KEY,
    customer_id INT NOT NULL REFERENCES analytics.customers(id) ON DELETE RESTRICT,
    status VARCHAR(30) NOT NULL, -- 'completed', 'shipped', 'pending', 'cancelled'
    total_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    order_date TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS analytics.order_items (
    id SERIAL PRIMARY KEY,
    order_id INT NOT NULL REFERENCES analytics.orders(id) ON DELETE CASCADE,
    product_id INT NOT NULL REFERENCES analytics.products(id) ON DELETE RESTRICT,
    quantity INT NOT NULL CHECK (quantity > 0),
    unit_price NUMERIC(10, 2) NOT NULL
);

-- 2. Populate Seed Data (Idempotent: only if empty)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM analytics.customers LIMIT 1) THEN
        INSERT INTO analytics.customers (first_name, last_name, email, country, city, created_at) VALUES
            ('Emma', 'Watson', 'emma.watson@example.com', 'United Kingdom', 'London', '2025-01-15 10:00:00+00'),
            ('Liam', 'Smith', 'liam.smith@example.com', 'United States', 'New York', '2025-01-20 11:30:00+00'),
            ('Sophia', 'Garcia', 'sophia.garcia@example.com', 'Spain', 'Madrid', '2025-02-01 09:15:00+00'),
            ('Noah', 'Müller', 'noah.mueller@example.com', 'Germany', 'Berlin', '2025-02-10 14:20:00+00'),
            ('Olivia', 'Chen', 'olivia.chen@example.com', 'Canada', 'Toronto', '2025-02-18 16:45:00+00'),
            ('Aarav', 'Patel', 'aarav.patel@example.com', 'India', 'Bengaluru', '2025-03-01 08:00:00+00'),
            ('Lucas', 'Silva', 'lucas.silva@example.com', 'Brazil', 'São Paulo', '2025-03-05 12:10:00+00'),
            ('Mia', 'Dubois', 'mia.dubois@example.com', 'France', 'Paris', '2025-03-12 15:30:00+00'),
            ('James', 'Wilson', 'james.wilson@example.com', 'United States', 'Austin', '2025-03-15 17:00:00+00'),
            ('Ananya', 'Sharma', 'ananya.sharma@example.com', 'India', 'Mumbai', '2025-03-20 18:25:00+00');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM analytics.products LIMIT 1) THEN
        INSERT INTO analytics.products (name, category, price, cost, stock_quantity, is_active, created_at) VALUES
            ('Pro Wireless Noise-Cancelling Headphones', 'Electronics', 249.99, 110.00, 150, TRUE, '2025-01-10 08:00:00+00'),
            ('Ultra HD 4K Monitor 27-inch', 'Electronics', 399.00, 220.00, 85, TRUE, '2025-01-10 08:00:00+00'),
            ('Ergonomic Mechanical Keyboard', 'Accessories', 129.50, 60.00, 200, TRUE, '2025-01-12 09:00:00+00'),
            ('Precision Wireless Mouse', 'Accessories', 59.99, 25.00, 310, TRUE, '2025-01-12 09:00:00+00'),
            ('Standing Desk Pro 60-inch', 'Furniture', 549.00, 300.00, 45, TRUE, '2025-01-15 10:00:00+00'),
            ('Ergonomic Mesh Office Chair', 'Furniture', 320.00, 160.00, 60, TRUE, '2025-01-15 10:00:00+00'),
            ('USB-C Multi-Port Docking Station', 'Electronics', 89.95, 38.00, 180, TRUE, '2025-01-18 11:00:00+00'),
            ('Aluminum Laptop Stand', 'Accessories', 39.99, 14.00, 250, TRUE, '2025-01-20 12:00:00+00'),
            ('Smart Fitness Watch Series 5', 'Wearables', 199.99, 85.00, 120, TRUE, '2025-02-01 10:00:00+00'),
            ('Waterproof Bluetooth Speaker', 'Audio', 79.99, 32.00, 140, TRUE, '2025-02-05 11:30:00+00');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM analytics.orders LIMIT 1) THEN
        INSERT INTO analytics.orders (id, customer_id, status, total_amount, order_date) VALUES
            (1001, 1, 'completed', 379.49, '2025-03-01 11:20:00+00'),
            (1002, 2, 'completed', 549.00, '2025-03-02 14:10:00+00'),
            (1003, 3, 'completed', 189.49, '2025-03-04 16:30:00+00'),
            (1004, 4, 'shipped',   399.00, '2025-03-05 09:45:00+00'),
            (1005, 5, 'completed', 798.99, '2025-03-08 13:15:00+00'),
            (1006, 6, 'pending',   129.50, '2025-03-10 10:05:00+00'),
            (1007, 7, 'completed', 320.00, '2025-03-12 15:40:00+00'),
            (1008, 8, 'cancelled', 249.99, '2025-03-14 17:20:00+00'),
            (1009, 9, 'completed', 458.99, '2025-03-18 12:00:00+00'),
            (1010, 10, 'shipped',  199.99, '2025-03-22 18:30:00+00');
        
        PERFORM setval('analytics.orders_id_seq', 1010);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM analytics.order_items LIMIT 1) THEN
        INSERT INTO analytics.order_items (order_id, product_id, quantity, unit_price) VALUES
            (1001, 1, 1, 249.99),
            (1001, 3, 1, 129.50),
            (1002, 5, 1, 549.00),
            (1003, 3, 1, 129.50),
            (1003, 4, 1, 59.99),
            (1004, 2, 1, 399.00),
            (1005, 2, 1, 399.00),
            (1005, 1, 1, 249.99),
            (1005, 8, 1, 39.99),
            (1005, 7, 1, 89.95),
            (1006, 3, 1, 129.50),
            (1007, 6, 1, 320.00),
            (1008, 1, 1, 249.99),
            (1009, 2, 1, 399.00),
            (1009, 4, 1, 59.99),
            (1010, 9, 1, 199.99);
    END IF;
END
$$;

-- 3. Explicitly Grant Privileges to analytics_user and app_user on these seeded tables
-- NOTE: analytics.customers table-level SELECT is intentionally excluded here to prevent
-- accidental table-level grants. Column-level permissions on customers are strictly managed
-- by 03_column_security_grants.sql.
GRANT SELECT ON analytics.products, analytics.orders, analytics.order_items TO analytics_user;
GRANT ALL ON ALL TABLES IN SCHEMA analytics TO app_user;
GRANT ALL ON ALL SEQUENCES IN SCHEMA analytics TO app_user;
