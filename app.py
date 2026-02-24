from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import os
from functools import wraps
import math
import cohere

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///marketplace.db')
if app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgres://'):
    app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI'].replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # buyer, rider, admin, seller
    phone_number = db.Column(db.String(20), nullable=False)
    location = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50))
    image_url = db.Column(db.String(200))
    stock = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    rider_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    total = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, in_transit, completed
    delivery_address = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', foreign_keys=[user_id], backref='orders')
    rider = db.relationship('User', foreign_keys=[rider_id], backref='deliveries')

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    
    order = db.relationship('Order', backref='items')
    product = db.relationship('Product')

class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reviewed_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    reviewer = db.relationship('User', foreign_keys=[reviewer_id])
    reviewed = db.relationship('User', foreign_keys=[reviewed_id])

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='posts')

# Initialize Cohere
cohere_api_key = os.environ.get('COHERE_API_KEY')
co = cohere.Client(cohere_api_key) if cohere_api_key else None

# Helper functions
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(role):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            user = User.query.get(session['user_id'])
            if user.role != role and user.role != 'admin':
                flash('Access denied.', 'danger')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def find_nearest_rider(location):
    riders = User.query.filter_by(role='rider').all()
    if not riders:
        return None
    # Simple assignment - in production, use geolocation
    available_riders = [r for r in riders if len(r.deliveries) < 5]
    return available_riders[0] if available_riders else riders[0]

# Routes
@app.route('/')
def index():
    products = Product.query.limit(12).all()
    return render_template('index.html', products=products)

@app.route('/products')
def products():
    category = request.args.get('category')
    search = request.args.get('search')
    
    query = Product.query
    if category:
        query = query.filter_by(category=category)
    if search:
        query = query.filter(Product.name.ilike(f'%{search}%'))
    
    products = query.all()
    categories = db.session.query(Product.category).distinct().all()
    return render_template('products.html', products=products, categories=[c[0] for c in categories])

@app.route('/product/<int:id>')
def product_detail(id):
    product = Product.query.get_or_404(id)
    return render_template('product_detail.html', product=product)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        role = request.form['role']
        phone_number = request.form['phone_number']
        location = request.form.get('location', '')
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('register'))
        
        user = User(
            username=username,
            email=email,
            password=generate_password_hash(password),
            role=role,
            phone_number=phone_number,
            location=location
        )
        db.session.add(user)
        db.session.commit()
        
        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['username'] = user.username
            session['role'] = user.role
            flash('Login successful!', 'success')
            
            if user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif user.role == 'rider':
                return redirect(url_for('rider_dashboard'))
            elif user.role == 'seller':
                return redirect(url_for('seller_dashboard'))
            else:
                return redirect(url_for('index'))
        
        flash('Invalid credentials.', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('index'))

@app.route('/cart')
@login_required
def cart():
    cart_items = session.get('cart', {})
    products = []
    total = 0
    
    for product_id, quantity in cart_items.items():
        product = Product.query.get(int(product_id))
        if product:
            products.append({'product': product, 'quantity': quantity})
            total += product.price * quantity
    
    return render_template('cart.html', products=products, total=total)

@app.route('/add_to_cart/<int:id>', methods=['POST'])
@login_required
def add_to_cart(id):
    quantity = int(request.form.get('quantity', 1))
    cart = session.get('cart', {})
    cart[str(id)] = cart.get(str(id), 0) + quantity
    session['cart'] = cart
    flash('Added to cart!', 'success')
    return redirect(url_for('products'))

@app.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    if request.method == 'POST':
        cart = session.get('cart', {})
        if not cart:
            flash('Cart is empty.', 'warning')
            return redirect(url_for('products'))
        
        delivery_address = request.form['delivery_address']
        user = User.query.get(session['user_id'])
        
        total = 0
        order = Order(
            user_id=user.id,
            total=0,
            status='pending',
            delivery_address=delivery_address
        )
        db.session.add(order)
        db.session.flush()
        
        for product_id, quantity in cart.items():
            product = Product.query.get(int(product_id))
            if product and product.stock >= quantity:
                item = OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    quantity=quantity,
                    price=product.price
                )
                product.stock -= quantity
                total += product.price * quantity
                db.session.add(item)
        
        order.total = total
        
        # Assign nearest rider
        rider = find_nearest_rider(delivery_address)
        if rider:
            order.rider_id = rider.id
        
        db.session.commit()
        session['cart'] = {}
        
        flash('Order placed successfully!', 'success')
        return redirect(url_for('orders'))
    
    cart = session.get('cart', {})
    products = []
    total = 0
    
    for product_id, quantity in cart.items():
        product = Product.query.get(int(product_id))
        if product:
            products.append({'product': product, 'quantity': quantity})
            total += product.price * quantity
    
    return render_template('checkout.html', products=products, total=total)

@app.route('/orders')
@login_required
def orders():
    user = User.query.get(session['user_id'])
    orders = Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).all()
    return render_template('orders.html', orders=orders)

@app.route('/rider/dashboard')
@role_required('rider')
def rider_dashboard():
    rider = User.query.get(session['user_id'])
    deliveries = Order.query.filter_by(rider_id=rider.id).order_by(Order.created_at.desc()).all()
    return render_template('rider_dashboard.html', deliveries=deliveries)

@app.route('/rider/update_status/<int:id>', methods=['POST'])
@role_required('rider')
def update_delivery_status(id):
    order = Order.query.get_or_404(id)
    if order.rider_id != session['user_id']:
        flash('Access denied.', 'danger')
        return redirect(url_for('rider_dashboard'))
    
    status = request.form['status']
    order.status = status
    db.session.commit()
    
    flash('Delivery status updated!', 'success')
    return redirect(url_for('rider_dashboard'))

@app.route('/seller/dashboard')
@role_required('seller')
def seller_dashboard():
    products = Product.query.all()
    orders = Order.query.order_by(Order.created_at.desc()).limit(20).all()
    stats = {
        'total_users': 0,
        'total_products': len(products),
        'total_orders': len(orders),
        'pending_orders': sum(1 for o in orders if o.status == 'pending')
    }
    return render_template('admin_dashboard.html', stats=stats, users=[], products=products, orders=orders, is_seller=True)

@app.route('/admin/dashboard')
@role_required('admin')
def admin_dashboard():
    users = User.query.all()
    products = Product.query.all()
    orders = Order.query.order_by(Order.created_at.desc()).limit(20).all()
    
    stats = {
        'total_users': len(users),
        'total_products': len(products),
        'total_orders': Order.query.count(),
        'pending_orders': Order.query.filter_by(status='pending').count()
    }
    
    return render_template('admin_dashboard.html', stats=stats, users=users, products=products, orders=orders)

@app.route('/admin/add_product', methods=['GET', 'POST'])
@role_required('seller')
def add_product():
    if request.method == 'POST':
        product = Product(
            name=request.form['name'],
            description=request.form['description'],
            price=float(request.form['price']),
            category=request.form['category'],
            image_url=request.form['image_url'],
            stock=int(request.form['stock'])
        )
        db.session.add(product)
        db.session.commit()
        
        flash('Product added successfully!', 'success')
        return redirect(url_for('admin_dashboard'))
    
    return render_template('add_product.html')

@app.route('/admin/edit_product/<int:id>', methods=['GET', 'POST'])
@role_required('seller')
def edit_product(id):
    product = Product.query.get_or_404(id)
    
    if request.method == 'POST':
        product.name = request.form['name']
        product.description = request.form['description']
        product.price = float(request.form['price'])
        product.category = request.form['category']
        product.image_url = request.form['image_url']
        product.stock = int(request.form['stock'])
        db.session.commit()
        
        flash('Product updated successfully!', 'success')
        return redirect(url_for('admin_dashboard'))
    
    return render_template('edit_product.html', product=product)

@app.route('/admin/delete_product/<int:id>')
@role_required('seller')
def delete_product(id):
    product = Product.query.get_or_404(id)
    db.session.delete(product)
    db.session.commit()
    flash('Product deleted successfully!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/review/<int:user_id>', methods=['GET', 'POST'])
@login_required
def review_user(user_id):
    reviewed_user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        review = Review(
            reviewer_id=session['user_id'],
            reviewed_id=user_id,
            rating=int(request.form['rating']),
            comment=request.form['comment']
        )
        db.session.add(review)
        db.session.commit()
        
        flash('Review submitted successfully!', 'success')
        return redirect(url_for('community'))
    
    return render_template('review.html', user=reviewed_user)

@app.route('/community', methods=['GET', 'POST'])
@login_required
def community():
    if request.method == 'POST':
        content = request.form['content']
        post = Post(user_id=session['user_id'], content=content)
        db.session.add(post)
        db.session.commit()
        flash('Post added to community!', 'success')
        return redirect(url_for('community'))
    
    posts = Post.query.order_by(Post.created_at.desc()).all()
    # Get all users so people can review them
    users = User.query.filter(User.id != session['user_id']).all()
    return render_template('community.html', posts=posts, users=users)

@app.route('/chat', methods=['GET', 'POST'])
@login_required
def chat():
    response_text = None
    user_message = ""
    if request.method == 'POST':
        user_message = request.form['message']
        if co:
            try:
                response = co.chat(
                    message=user_message,
                    model="command"
                )
                response_text = response.text
            except Exception as e:
                response_text = f"Error communicating with AI: {str(e)}"
        else:
            response_text = "Cohere API key is not configured."
            
    return render_template('chat.html', response=response_text, user_message=user_message)

@app.route('/rider/map/<int:order_id>')
@role_required('rider')
def rider_map(order_id):
    order = Order.query.get_or_404(order_id)
    if order.rider_id != session['user_id']:
        flash('Access denied.', 'danger')
        return redirect(url_for('rider_dashboard'))
    return render_template('rider_map.html', order=order)

# Initialize database and seed data
def init_db():
    with app.app_context():
        db.create_all()
        
        # Check if admin exists
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin',
                email='admin@marketplace.com',
                password=generate_password_hash('admin123'),
                role='admin',
                phone_number='1234567890',
                location='Main Office'
            )
            db.session.add(admin)
            
            # Add sample products
            products = [
                Product(name='Fresh Apples', description='Crisp and sweet red apples', price=3.99, category='Fruits', 
                       image_url='https://images.unsplash.com/photo-1560806887-1e4cd0b6cbd6?w=400', stock=50),
                Product(name='Organic Bananas', description='Yellow ripe bananas', price=2.49, category='Fruits',
                       image_url='https://images.unsplash.com/photo-1603833665858-e61d17a86224?w=400', stock=100),
                Product(name='Fresh Tomatoes', description='Ripe red tomatoes', price=4.99, category='Vegetables',
                       image_url='https://images.unsplash.com/photo-1546094096-0df4bcaaa337?w=400', stock=75),
                Product(name='Green Lettuce', description='Fresh crispy lettuce', price=2.99, category='Vegetables',
                       image_url='https://images.unsplash.com/photo-1622206151226-18ca2c9ab4a1?w=400', stock=60),
                Product(name='Orange Juice', description='Fresh squeezed orange juice', price=5.99, category='Beverages',
                       image_url='https://images.unsplash.com/photo-1600271886742-f049cd451bba?w=400', stock=40),
                Product(name='Fresh Carrots', description='Crunchy orange carrots', price=3.49, category='Vegetables',
                       image_url='https://images.unsplash.com/photo-1598170845058-32b9d6a5da37?w=400', stock=80),
                Product(name='Strawberries', description='Sweet red strawberries', price=6.99, category='Fruits',
                       image_url='https://images.unsplash.com/photo-1464965911861-746a04b4bca6?w=400', stock=45),
                Product(name='Bell Peppers', description='Colorful bell peppers', price=4.49, category='Vegetables',
                       image_url='https://images.unsplash.com/photo-1563565375-f3fdfdbefa83?w=400', stock=55),
            ]
            
            for product in products:
                db.session.add(product)
            
            # Add sample rider
            rider = User(
                username='rider1',
                email='rider@marketplace.com',
                password=generate_password_hash('rider123'),
                role='rider',
                phone_number='0987654321',
                location='Downtown'
            )
            db.session.add(rider)
            
            db.session.commit()
            print('Database initialized with sample data!')

# ===== AUTO-CREATE TABLES ON STARTUP =====
# This runs whenever the app starts on Render
with app.app_context():
    try:
        # Create all tables if they don't exist
        db.create_all()
        print("✅ Database tables created/verified successfully!")
        
        # Check if we need to seed sample data
        if not User.query.filter_by(username='admin').first():
            print("📦 Adding sample data...")
            
            # Add admin user
            admin = User(
                username='admin',
                email='admin@marketplace.com',
                password=generate_password_hash('admin123'),
                role='admin',
                phone_number='1234567890',
                location='Main Office'
            )
            db.session.add(admin)
            
            # Add sample products
            products = [
                Product(name='Fresh Apples', description='Crisp and sweet red apples', price=3.99, category='Fruits', 
                       image_url='https://images.unsplash.com/photo-1560806887-1e4cd0b6cbd6?w=400', stock=50),
                Product(name='Organic Bananas', description='Yellow ripe bananas', price=2.49, category='Fruits',
                       image_url='https://images.unsplash.com/photo-1603833665858-e61d17a86224?w=400', stock=100),
                Product(name='Fresh Tomatoes', description='Ripe red tomatoes', price=4.99, category='Vegetables',
                       image_url='https://images.unsplash.com/photo-1546094096-0df4bcaaa337?w=400', stock=75),
                Product(name='Green Lettuce', description='Fresh crispy lettuce', price=2.99, category='Vegetables',
                       image_url='https://images.unsplash.com/photo-1622206151226-18ca2c9ab4a1?w=400', stock=60),
                Product(name='Orange Juice', description='Fresh squeezed orange juice', price=5.99, category='Beverages',
                       image_url='https://images.unsplash.com/photo-1600271886742-f049cd451bba?w=400', stock=40),
                Product(name='Fresh Carrots', description='Crunchy orange carrots', price=3.49, category='Vegetables',
                       image_url='https://images.unsplash.com/photo-1598170845058-32b9d6a5da37?w=400', stock=80),
                Product(name='Strawberries', description='Sweet red strawberries', price=6.99, category='Fruits',
                       image_url='https://images.unsplash.com/photo-1464965911861-746a04b4bca6?w=400', stock=45),
                Product(name='Bell Peppers', description='Colorful bell peppers', price=4.49, category='Vegetables',
                       image_url='https://images.unsplash.com/photo-1563565375-f3fdfdbefa83?w=400', stock=55),
            ]
            
            for product in products:
                db.session.add(product)
            
            # Add sample rider
            rider = User(
                username='rider1',
                email='rider@marketplace.com',
                password=generate_password_hash('rider123'),
                role='rider',
                phone_number='0987654321',
                location='Downtown'
            )
            db.session.add(rider)
            
            db.session.commit()
            print("✅ Sample data added successfully!")
        else:
            print("ℹ️ Database already has data, skipping sample data")
            
    except Exception as e:
        print(f"⚠️ Database initialization error: {e}")

if __name__ == '__main__':
    # Don't call init_db() here - it's already called above
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=os.environ.get('FLASK_ENV') == 'development')
