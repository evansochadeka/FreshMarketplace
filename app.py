from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, abort, Response
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import os
from functools import wraps
import math
import cohere
import uuid
import json
import hashlib
import hmac
import base64
import time

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///marketplace.db')
if app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgres://'):
    app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI'].replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# SocketIO for real-time features
socketio = SocketIO(app, cors_allowed_origins="*", manage_session=False)

# File upload configuration
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Video call configuration
VIDEO_CALL_TIMEOUT = 3600  # 1 hour
VIDEO_CALL_KEY = os.environ.get('VIDEO_CALL_KEY', 'your-secret-key-for-video-calls')

# Create upload folder if it doesn't exist
os.makedirs(os.path.join(app.root_path, UPLOAD_FOLDER), exist_ok=True)

db = SQLAlchemy(app)

# Models (keeping all existing models, adding new ones)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # buyer, rider, admin, seller
    phone_number = db.Column(db.String(20), nullable=False)
    whatsapp_number = db.Column(db.String(20))  # For WhatsApp integration
    location = db.Column(db.String(200))
    latitude = db.Column(db.Float, nullable=True)  # For real-time tracking
    longitude = db.Column(db.Float, nullable=True)
    profile_image = db.Column(db.String(500), default='/static/images/default-profile.png')
    business_name = db.Column(db.String(200))
    business_address = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    is_online = db.Column(db.Boolean, default=False)
    
    # Relationships
    products = db.relationship('Product', backref='seller', lazy=True)
    sales = db.relationship('Sale', backref='seller', foreign_keys='Sale.seller_id', lazy=True)
    offline_sales = db.relationship('OfflineSale', backref='seller', lazy=True)
    customers = db.relationship('Customer', backref='seller', lazy=True)
    locations = db.relationship('UserLocation', backref='user', lazy=True, order_by='UserLocation.timestamp.desc()')

class UserLocation(db.Model):
    """Track user location history"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    accuracy = db.Column(db.Float)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    base_price = db.Column(db.Float, nullable=False)
    price = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50))
    image_url = db.Column(db.String(500))
    additional_images = db.Column(db.JSON, default=[])  # Multiple product images
    stock = db.Column(db.Integer, default=0)
    sku = db.Column(db.String(50), unique=True)
    barcode = db.Column(db.String(100))
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    low_stock_threshold = db.Column(db.Integer, default=5)
    video_call_enabled = db.Column(db.Boolean, default=True)  # Allow video calls for this product
    virtual_tour_url = db.Column(db.String(500))  # Optional 360 view

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    rider_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    total = db.Column(db.Float, nullable=False)
    subtotal = db.Column(db.Float, nullable=False)
    rider_fee = db.Column(db.Float, default=0)
    platform_fee = db.Column(db.Float, default=0)
    status = db.Column(db.String(20), default='pending')
    delivery_address = db.Column(db.String(200))
    delivery_lat = db.Column(db.Float)  # Delivery location for maps
    delivery_lng = db.Column(db.Float)
    payment_method = db.Column(db.String(50), default='cash')
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    
    user = db.relationship('User', foreign_keys=[user_id], backref='orders')
    rider = db.relationship('User', foreign_keys=[rider_id], backref='deliveries')
    seller = db.relationship('User', foreign_keys=[seller_id], backref='seller_orders')
    tracking = db.relationship('OrderTracking', backref='order', lazy=True)

class OrderTracking(db.Model):
    """Real-time order tracking"""
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    rider_lat = db.Column(db.Float)
    rider_lng = db.Column(db.Float)
    status = db.Column(db.String(20))
    message = db.Column(db.String(200))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class VideoCall(db.Model):
    """Video call sessions"""
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.String(100), unique=True, nullable=False)
    initiator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    status = db.Column(db.String(20), default='pending')  # pending, active, ended
    started_at = db.Column(db.DateTime)
    ended_at = db.Column(db.DateTime)
    ai_assistant_active = db.Column(db.Boolean, default=False)  # AI in call
    recording_enabled = db.Column(db.Boolean, default=False)
    
    initiator = db.relationship('User', foreign_keys=[initiator_id])
    receiver = db.relationship('User', foreign_keys=[receiver_id])
    product = db.relationship('Product')

class VideoCallMessage(db.Model):
    """Messages during video calls"""
    id = db.Column(db.Integer, primary_key=True)
    call_id = db.Column(db.Integer, db.ForeignKey('video_call.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.Text)
    is_ai = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    seller_price = db.Column(db.Float, nullable=False)
    
    order = db.relationship('Order', backref='items')
    product = db.relationship('Product')
    seller = db.relationship('User', foreign_keys=[seller_id])

class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'))
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    profit = db.Column(db.Float)
    sale_type = db.Column(db.String(20), default='online')
    status = db.Column(db.String(20), default='completed')
    sale_date = db.Column(db.DateTime, default=datetime.utcnow)
    payment_method = db.Column(db.String(50))
    notes = db.Column(db.Text)

class OfflineSale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    customer_name = db.Column(db.String(100))
    customer_phone = db.Column(db.String(20))
    items = db.Column(db.JSON)
    subtotal = db.Column(db.Float, nullable=False)
    tax = db.Column(db.Float, default=0)
    discount = db.Column(db.Float, default=0)
    total = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(50))
    status = db.Column(db.String(20), default='completed')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    receipt_number = db.Column(db.String(50), unique=True)

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    whatsapp = db.Column(db.String(20))
    email = db.Column(db.String(120))
    address = db.Column(db.String(200))
    total_purchases = db.Column(db.Float, default=0)
    visit_count = db.Column(db.Integer, default=0)
    last_visit = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reviewed_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    reviewer = db.relationship('User', foreign_keys=[reviewer_id])
    reviewed = db.relationship('User', foreign_keys=[reviewed_id])
    order = db.relationship('Order')

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    title = db.Column(db.String(200))
    post_type = db.Column(db.String(20), default='general')
    likes = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = db.relationship('User', backref='posts')
    comments = db.relationship('Comment', backref='post', lazy=True)

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User')

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    sender = db.relationship('User', foreign_keys=[sender_id])
    receiver = db.relationship('User', foreign_keys=[receiver_id])
    product = db.relationship('Product')

class InventoryLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    previous_stock = db.Column(db.Integer)
    new_stock = db.Column(db.Integer)
    change = db.Column(db.Integer)
    reason = db.Column(db.String(100))
    reference_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    product = db.relationship('Product')
    seller = db.relationship('User')

# Initialize Cohere
cohere_api_key = os.environ.get('COHERE_API_KEY')
co = cohere.Client(cohere_api_key) if cohere_api_key else None

# Helper functions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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

def calculate_price_with_fees(base_price):
    rider_fee = base_price * 0.10
    platform_fee = base_price * 0.10
    final_price = base_price + rider_fee + platform_fee
    return round(final_price, 2), round(rider_fee, 2), round(platform_fee, 2)

def find_nearest_rider(location):
    riders = User.query.filter_by(role='rider', is_online=True).all()
    if not riders:
        return None
    # In production, use actual geolocation distance calculation
    return riders[0] if riders else None

def log_inventory_change(product_id, seller_id, previous_stock, new_stock, reason, reference_id=None):
    change = new_stock - previous_stock
    log = InventoryLog(
        product_id=product_id,
        seller_id=seller_id,
        previous_stock=previous_stock,
        new_stock=new_stock,
        change=change,
        reason=reason,
        reference_id=reference_id
    )
    db.session.add(log)
    db.session.commit()

def generate_receipt_number():
    return f"REC-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

def generate_video_room_id():
    """Generate unique room ID for video calls"""
    return f"room-{uuid.uuid4().hex[:12]}"

def create_superuser():
    with app.app_context():
        superuser = User.query.filter_by(username='benedict431').first()
        if not superuser:
            admin = User(
                username='benedict431',
                email='benedict431@admin.com',
                password=generate_password_hash('28734495'),
                role='admin',
                phone_number='+254700000000',
                whatsapp_number='+254700000000',
                location='Nairobi, Kenya',
                profile_image='/static/images/default-profile.png',
                business_name='Super Admin',
                business_address='Admin Office'
            )
            db.session.add(admin)
            
            try:
                db.session.commit()
                print("✅ Superuser 'benedict431' created successfully!")
            except Exception as e:
                db.session.rollback()
                print(f"❌ Error creating superuser: {e}")
        else:
            print("ℹ️ Superuser 'benedict431' already exists")

# Routes
@app.route('/')
def index():
    products = Product.query.filter_by(is_active=True).limit(12).all()
    return render_template('index.html', products=products)

@app.route('/products')
def products():
    category = request.args.get('category')
    search = request.args.get('search')
    seller_id = request.args.get('seller')
    
    query = Product.query.filter_by(is_active=True)
    if category:
        query = query.filter_by(category=category)
    if search:
        query = query.filter(Product.name.ilike(f'%{search}%'))
    if seller_id:
        query = query.filter_by(seller_id=seller_id)
    
    products = query.all()
    categories = db.session.query(Product.category).distinct().all()
    return render_template('products.html', products=products, categories=[c[0] for c in categories])

@app.route('/product/<int:id>')
def product_detail(id):
    product = Product.query.get_or_404(id)
    seller = User.query.get(product.seller_id)
    related_products = Product.query.filter_by(category=product.category, is_active=True).filter(Product.id != product.id).limit(4).all()
    return render_template('product_detail.html', product=product, seller=seller, related_products=related_products)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form.get('confirm_password')
        role = request.form['role']
        phone_number = request.form['phone_number']
        whatsapp_number = request.form.get('whatsapp_number', phone_number)
        location = request.form.get('location', '')
        business_name = request.form.get('business_name', '') if role == 'seller' else None
        business_address = request.form.get('business_address', '') if role == 'seller' else None
        
        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('register'))
        
        profile_image = '/static/images/default-profile.png'
        if 'profile_image' in request.files:
            file = request.files['profile_image']
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(f"{username}_{uuid.uuid4().hex[:8]}_{file.filename}")
                file.save(os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], filename))
                profile_image = f'/static/uploads/{filename}'
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('register'))
        
        if User.query.filter_by(email=email).first():
            flash('Email already exists.', 'danger')
            return redirect(url_for('register'))
        
        user = User(
            username=username,
            email=email,
            password=generate_password_hash(password),
            role=role,
            phone_number=phone_number,
            whatsapp_number=whatsapp_number,
            location=location,
            profile_image=profile_image,
            business_name=business_name,
            business_address=business_address
        )
        
        try:
            db.session.add(user)
            db.session.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            flash(f'Registration failed: {str(e)}', 'danger')
            return redirect(url_for('register'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        remember = request.form.get('remember') == 'on'
        
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password, password):
            session.permanent = remember
            session['user_id'] = user.id
            session['username'] = user.username
            session['role'] = user.role
            session['profile_image'] = user.profile_image
            
            # Update online status
            user.is_online = True
            user.last_seen = datetime.utcnow()
            db.session.commit()
            
            unread_count = Message.query.filter_by(receiver_id=user.id, is_read=False).count()
            session['unread_count'] = unread_count
            
            flash(f'Welcome back, {user.username}!', 'success')
            
            if user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif user.role == 'rider':
                return redirect(url_for('rider_dashboard'))
            elif user.role == 'seller':
                return redirect(url_for('seller_dashboard'))
            else:
                return redirect(url_for('index'))
        else:
            flash('Invalid username or password.', 'danger')
            return redirect(url_for('login'))
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        if user:
            user.is_online = False
            user.last_seen = datetime.utcnow()
            db.session.commit()
    
    session.clear()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('index'))

# ===== RIDER TRACKING AND MAPS =====

@app.route('/rider/track/<int:order_id>')
@login_required
def track_order(order_id):
    """Track rider location for an order"""
    order = Order.query.get_or_404(order_id)
    user = User.query.get(session['user_id'])
    
    # Check permission
    if user.role != 'admin' and order.user_id != user.id and order.rider_id != user.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    return render_template('track_order.html', order=order)

@app.route('/rider/update-location', methods=['POST'])
@login_required
@role_required('rider')
def update_rider_location():
    """Update rider's current location (called from mobile)"""
    data = request.get_json()
    rider_id = session['user_id']
    lat = data.get('lat')
    lng = data.get('lng')
    accuracy = data.get('accuracy', 0)
    order_id = data.get('order_id')  # Optional - specific order being delivered
    
    rider = User.query.get(rider_id)
    rider.latitude = lat
    rider.longitude = lng
    rider.last_seen = datetime.utcnow()
    
    # Save location history
    location = UserLocation(
        user_id=rider_id,
        latitude=lat,
        longitude=lng,
        accuracy=accuracy
    )
    db.session.add(location)
    
    # If this is for a specific order, update order tracking
    if order_id:
        tracking = OrderTracking(
            order_id=order_id,
            rider_lat=lat,
            rider_lng=lng,
            status='in_transit',
            timestamp=datetime.utcnow()
        )
        db.session.add(tracking)
        
        # Emit real-time update via SocketIO
        socketio.emit(f'order_{order_id}_location', {
            'lat': lat,
            'lng': lng,
            'timestamp': datetime.utcnow().isoformat()
        }, room=f'order_{order_id}')
    
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/api/rider-location/<int:rider_id>')
def get_rider_location(rider_id):
    """Get rider's current location"""
    rider = User.query.get_or_404(rider_id)
    return jsonify({
        'lat': rider.latitude,
        'lng': rider.longitude,
        'last_seen': rider.last_seen.isoformat() if rider.last_seen else None,
        'is_online': rider.is_online
    })

@app.route('/api/order-tracking/<int:order_id>')
@login_required
def get_order_tracking(order_id):
    """Get tracking history for an order"""
    order = Order.query.get_or_404(order_id)
    tracking = OrderTracking.query.filter_by(order_id=order_id).order_by(OrderTracking.timestamp).all()
    
    return jsonify([{
        'lat': t.rider_lat,
        'lng': t.rider_lng,
        'status': t.status,
        'message': t.message,
        'timestamp': t.timestamp.isoformat()
    } for t in tracking])

# ===== VIDEO CALL FEATURES =====

@app.route('/video-call/initiate', methods=['POST'])
@login_required
def initiate_video_call():
    """Initiate a video call between buyer and seller"""
    data = request.get_json()
    receiver_id = data.get('receiver_id')
    product_id = data.get('product_id')
    
    receiver = User.query.get_or_404(receiver_id)
    product = Product.query.get(product_id) if product_id else None
    
    # Check if receiver is online
    if not receiver.is_online:
        return jsonify({'error': 'User is offline'}), 400
    
    # Create video call room
    room_id = generate_video_room_id()
    
    video_call = VideoCall(
        room_id=room_id,
        initiator_id=session['user_id'],
        receiver_id=receiver_id,
        product_id=product_id,
        status='pending',
        ai_assistant_active=False
    )
    db.session.add(video_call)
    db.session.commit()
    
    # Notify receiver via SocketIO
    socketio.emit('incoming_call', {
        'call_id': video_call.id,
        'room_id': room_id,
        'initiator': session['username'],
        'product': product.name if product else None,
        'initiator_id': session['user_id']
    }, room=f'user_{receiver_id}')
    
    return jsonify({
        'success': True,
        'call_id': video_call.id,
        'room_id': room_id
    })

@app.route('/video-call/<int:call_id>')
@login_required
def video_call_room(call_id):
    """Video call room page"""
    call = VideoCall.query.get_or_404(call_id)
    
    # Check permission
    if call.initiator_id != session['user_id'] and call.receiver_id != session['user_id']:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    # Update call status
    if call.status == 'pending':
        call.status = 'active'
        call.started_at = datetime.utcnow()
        db.session.commit()
    
    product = Product.query.get(call.product_id) if call.product_id else None
    other_user = User.query.get(call.initiator_id if call.receiver_id == session['user_id'] else call.receiver_id)
    
    return render_template('video_call.html', 
                         call=call, 
                         product=product,
                         other_user=other_user,
                         api_key=VIDEO_CALL_KEY)

@app.route('/video-call/<int:call_id>/end', methods=['POST'])
@login_required
def end_video_call(call_id):
    """End a video call"""
    call = VideoCall.query.get_or_404(call_id)
    
    if call.initiator_id != session['user_id'] and call.receiver_id != session['user_id']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    call.status = 'ended'
    call.ended_at = datetime.utcnow()
    db.session.commit()
    
    # Notify other party
    other_id = call.receiver_id if call.initiator_id == session['user_id'] else call.initiator_id
    socketio.emit('call_ended', {
        'call_id': call_id
    }, room=f'user_{other_id}')
    
    return jsonify({'success': True})

@app.route('/video-call/<int:call_id>/ai-toggle', methods=['POST'])
@login_required
def toggle_video_call_ai(call_id):
    """Toggle AI assistant in video call"""
    call = VideoCall.query.get_or_404(call_id)
    
    if call.initiator_id != session['user_id'] and call.receiver_id != session['user_id']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    call.ai_assistant_active = data.get('active', not call.ai_assistant_active)
    db.session.commit()
    
    # Notify participants
    socketio.emit('ai_toggled', {
        'call_id': call_id,
        'active': call.ai_assistant_active
    }, room=f'call_{call_id}')
    
    return jsonify({'success': True, 'active': call.ai_assistant_active})

@app.route('/video-call/<int:call_id>/message', methods=['POST'])
@login_required
def send_video_call_message(call_id):
    """Send a message during video call"""
    call = VideoCall.query.get_or_404(call_id)
    
    if call.initiator_id != session['user_id'] and call.receiver_id != session['user_id']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    message = data.get('message')
    is_ai = data.get('is_ai', False)
    
    # If AI is active, process message with Cohere
    if call.ai_assistant_active and co and not is_ai:
        try:
            # Get AI response
            response = co.chat(
                message=message,
                model="command",
                preamble="You are an AI assistant in a video call helping with product inquiries and negotiations."
            )
            
            ai_message = VideoCallMessage(
                call_id=call_id,
                sender_id=session['user_id'],
                message=response.text if hasattr(response, 'text') else str(response),
                is_ai=True
            )
            db.session.add(ai_message)
            db.session.commit()
            
            # Send AI response to all participants
            socketio.emit('call_message', {
                'call_id': call_id,
                'message': ai_message.message,
                'sender': 'AI Assistant',
                'is_ai': True,
                'timestamp': datetime.utcnow().isoformat()
            }, room=f'call_{call_id}')
            
        except Exception as e:
            print(f"AI Error: {e}")
    
    # Save user message
    user_message = VideoCallMessage(
        call_id=call_id,
        sender_id=session['user_id'],
        message=message,
        is_ai=False
    )
    db.session.add(user_message)
    db.session.commit()
    
    # Broadcast to all participants
    socketio.emit('call_message', {
        'call_id': call_id,
        'message': message,
        'sender': session['username'],
        'is_ai': False,
        'timestamp': datetime.utcnow().isoformat()
    }, room=f'call_{call_id}')
    
    return jsonify({'success': True})

# ===== SELLER DASHBOARD & CRM FEATURES =====

@app.route('/seller/dashboard')
@role_required('seller')
def seller_dashboard():
    seller = User.query.get(session['user_id'])
    
    products = Product.query.filter_by(seller_id=seller.id, is_active=True).all()
    
    today = datetime.now().date()
    
    today_sales = db.session.query(db.func.sum(Sale.total_price)).filter(
        Sale.seller_id == seller.id,
        db.func.date(Sale.sale_date) == today
    ).scalar() or 0
    
    week_start = today - timedelta(days=today.weekday())
    week_sales = db.session.query(db.func.sum(Sale.total_price)).filter(
        Sale.seller_id == seller.id,
        Sale.sale_date >= week_start
    ).scalar() or 0
    
    month_start = today.replace(day=1)
    month_sales = db.session.query(db.func.sum(Sale.total_price)).filter(
        Sale.seller_id == seller.id,
        Sale.sale_date >= month_start
    ).scalar() or 0
    
    total_sales = db.session.query(db.func.sum(Sale.total_price)).filter(
        Sale.seller_id == seller.id
    ).scalar() or 0
    
    recent_orders = Order.query.filter_by(seller_id=seller.id).order_by(Order.created_at.desc()).limit(10).all()
    low_stock_products = [p for p in products if p.stock <= p.low_stock_threshold]
    
    top_products = db.session.query(
        Product, db.func.sum(OrderItem.quantity).label('total_sold')
    ).join(OrderItem).join(Order).filter(
        Product.seller_id == seller.id,
        Order.status == 'completed'
    ).group_by(Product).order_by(db.desc('total_sold')).limit(5).all()
    
    recent_customers = Customer.query.filter_by(seller_id=seller.id).order_by(Customer.last_visit.desc()).limit(10).all()
    unread_messages = Message.query.filter_by(receiver_id=seller.id, is_read=False).count()
    
    stats = {
        'total_products': len(products),
        'total_sales': total_sales,
        'today_sales': today_sales,
        'week_sales': week_sales,
        'month_sales': month_sales,
        'low_stock_count': len(low_stock_products),
        'unread_messages': unread_messages
    }
    
    return render_template('seller_dashboard.html', 
                         seller=seller,
                         products=products,
                         stats=stats,
                         recent_orders=recent_orders,
                         low_stock_products=low_stock_products,
                         top_products=top_products,
                         recent_customers=recent_customers)

@app.route('/seller/products')
@role_required('seller')
def seller_products():
    seller = User.query.get(session['user_id'])
    show_all = request.args.get('show_all', 'false').lower() == 'true'
    
    if show_all:
        products = Product.query.filter_by(is_active=True).all()
        comparison_mode = True
    else:
        products = Product.query.filter_by(seller_id=seller.id, is_active=True).all()
        comparison_mode = False
    
    return render_template('seller_products.html', 
                         products=products, 
                         seller=seller,
                         comparison_mode=comparison_mode)

@app.route('/seller/add_product', methods=['GET', 'POST'])
@role_required('seller')
def add_product():
    if request.method == 'POST':
        name = request.form['name']
        description = request.form['description']
        base_price = float(request.form['base_price'])
        category = request.form['category']
        stock = int(request.form['stock'])
        sku = request.form.get('sku', f"SKU-{uuid.uuid4().hex[:8].upper()}")
        barcode = request.form.get('barcode', '')
        low_stock_threshold = int(request.form.get('low_stock_threshold', 5))
        video_call_enabled = request.form.get('video_call_enabled') == 'on'
        virtual_tour_url = request.form.get('virtual_tour_url', '')
        
        final_price, rider_fee, platform_fee = calculate_price_with_fees(base_price)
        
        image_url = '/static/images/default-product.png'
        if 'product_image' in request.files:
            file = request.files['product_image']
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(f"{name}_{uuid.uuid4().hex[:8]}_{file.filename}")
                file.save(os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], filename))
                image_url = f'/static/uploads/{filename}'
        elif request.form.get('image_url'):
            image_url = request.form['image_url']
        
        # Handle additional images
        additional_images = []
        if 'additional_images' in request.files:
            files = request.files.getlist('additional_images')
            for file in files:
                if file and file.filename and allowed_file(file.filename):
                    filename = secure_filename(f"{name}_extra_{uuid.uuid4().hex[:8]}_{file.filename}")
                    file.save(os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], filename))
                    additional_images.append(f'/static/uploads/{filename}')
        
        product = Product(
            name=name,
            description=description,
            base_price=base_price,
            price=final_price,
            category=category,
            image_url=image_url,
            additional_images=additional_images,
            stock=stock,
            sku=sku,
            barcode=barcode,
            seller_id=session['user_id'],
            low_stock_threshold=low_stock_threshold,
            video_call_enabled=video_call_enabled,
            virtual_tour_url=virtual_tour_url
        )
        
        db.session.add(product)
        db.session.commit()
        
        log_inventory_change(product.id, session['user_id'], 0, stock, 'initial_stock')
        
        flash(f'Product added successfully! Final price: Kes{final_price} (includes fees)', 'success')
        return redirect(url_for('seller_products'))
    
    return render_template('add_product.html')

@app.route('/seller/edit_product/<int:id>', methods=['GET', 'POST'])
@role_required('seller')
def edit_product(id):
    product = Product.query.get_or_404(id)
    
    if product.seller_id != session['user_id']:
        flash('You do not have permission to edit this product.', 'danger')
        return redirect(url_for('seller_products'))
    
    if request.method == 'POST':
        previous_stock = product.stock
        
        product.name = request.form['name']
        product.description = request.form['description']
        product.base_price = float(request.form['base_price'])
        final_price, rider_fee, platform_fee = calculate_price_with_fees(product.base_price)
        product.price = final_price
        product.category = request.form['category']
        product.stock = int(request.form['stock'])
        product.sku = request.form.get('sku', product.sku)
        product.barcode = request.form.get('barcode', '')
        product.low_stock_threshold = int(request.form.get('low_stock_threshold', 5))
        product.video_call_enabled = request.form.get('video_call_enabled') == 'on'
        product.virtual_tour_url = request.form.get('virtual_tour_url', '')
        
        if 'product_image' in request.files:
            file = request.files['product_image']
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(f"{product.name}_{uuid.uuid4().hex[:8]}_{file.filename}")
                file.save(os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], filename))
                product.image_url = f'/static/uploads/{filename}'
        elif request.form.get('image_url'):
            product.image_url = request.form['image_url']
        
        db.session.commit()
        
        if product.stock != previous_stock:
            log_inventory_change(product.id, session['user_id'], previous_stock, product.stock, 'manual_update')
        
        flash('Product updated successfully!', 'success')
        return redirect(url_for('seller_products'))
    
    return render_template('edit_product.html', product=product)

@app.route('/seller/delete_product/<int:id>')
@role_required('seller')
def delete_product(id):
    product = Product.query.get_or_404(id)
    
    if product.seller_id != session['user_id']:
        flash('You do not have permission to delete this product.', 'danger')
        return redirect(url_for('seller_products'))
    
    product.is_active = False
    db.session.commit()
    
    flash('Product deleted successfully!', 'success')
    return redirect(url_for('seller_products'))

@app.route('/seller/inventory')
@role_required('seller')
def seller_inventory():
    seller = User.query.get(session['user_id'])
    products = Product.query.filter_by(seller_id=seller.id, is_active=True).all()
    
    logs = InventoryLog.query.filter_by(seller_id=seller.id).order_by(InventoryLog.created_at.desc()).limit(50).all()
    
    total_stock_value = sum(p.price * p.stock for p in products)
    total_stock_cost = sum(p.base_price * p.stock for p in products)
    
    stats = {
        'total_products': len(products),
        'total_stock_value': total_stock_value,
        'total_stock_cost': total_stock_cost,
        'potential_profit': total_stock_value - total_stock_cost,
        'low_stock_count': sum(1 for p in products if p.stock <= p.low_stock_threshold)
    }
    
    return render_template('seller_inventory.html', 
                         products=products, 
                         logs=logs,
                         stats=stats)

@app.route('/seller/restock/<int:id>', methods=['POST'])
@role_required('seller')
def restock_product(id):
    product = Product.query.get_or_404(id)
    
    if product.seller_id != session['user_id']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    quantity = int(request.form.get('quantity', 0))
    if quantity <= 0:
        flash('Invalid quantity.', 'danger')
        return redirect(url_for('seller_inventory'))
    
    previous_stock = product.stock
    product.stock += quantity
    db.session.commit()
    
    log_inventory_change(product.id, session['user_id'], previous_stock, product.stock, 'restock')
    
    flash(f'Added {quantity} units to {product.name}', 'success')
    return redirect(url_for('seller_inventory'))

# ===== POS / OFFLINE SALES FEATURES =====

@app.route('/seller/pos')
@role_required('seller')
def pos_dashboard():
    seller = User.query.get(session['user_id'])
    products = Product.query.filter_by(seller_id=seller.id, is_active=True).filter(Product.stock > 0).all()
    
    today = datetime.now().date()
    today_sales = OfflineSale.query.filter(
        OfflineSale.seller_id == seller.id,
        db.func.date(OfflineSale.created_at) == today
    ).all()
    
    today_total = sum(sale.total for sale in today_sales)
    
    return render_template('pos_dashboard.html', 
                         products=products,
                         today_sales=today_sales,
                         today_total=today_total)

@app.route('/seller/pos/checkout', methods=['POST'])
@role_required('seller')
def pos_checkout():
    seller = User.query.get(session['user_id'])
    
    data = request.get_json()
    cart = data.get('cart', [])
    customer_name = data.get('customer_name', '')
    customer_phone = data.get('customer_phone', '')
    customer_whatsapp = data.get('customer_whatsapp', customer_phone)
    payment_method = data.get('payment_method', 'cash')
    discount = float(data.get('discount', 0))
    
    if not cart:
        return jsonify({'error': 'Cart is empty'}), 400
    
    items = []
    subtotal = 0
    
    for item in cart:
        product = Product.query.get(item['product_id'])
        if not product or product.seller_id != seller.id:
            return jsonify({'error': f'Invalid product: {item["product_id"]}'}), 400
        
        if product.stock < item['quantity']:
            return jsonify({'error': f'Insufficient stock for {product.name}'}), 400
        
        previous_stock = product.stock
        product.stock -= item['quantity']
        
        item_total = product.price * item['quantity']
        subtotal += item_total
        
        items.append({
            'product_id': product.id,
            'product_name': product.name,
            'quantity': item['quantity'],
            'unit_price': product.price,
            'total': item_total
        })
        
        log_inventory_change(product.id, seller.id, previous_stock, product.stock, 'pos_sale')
        
        sale = Sale(
            seller_id=seller.id,
            product_id=product.id,
            quantity=item['quantity'],
            unit_price=product.price,
            total_price=item_total,
            sale_type='pos',
            payment_method=payment_method
        )
        db.session.add(sale)
    
    total = subtotal - discount
    
    receipt_number = generate_receipt_number()
    offline_sale = OfflineSale(
        seller_id=seller.id,
        customer_name=customer_name,
        customer_phone=customer_phone,
        items=items,
        subtotal=subtotal,
        discount=discount,
        total=total,
        payment_method=payment_method,
        receipt_number=receipt_number
    )
    db.session.add(offline_sale)
    
    if customer_name:
        customer = Customer.query.filter_by(seller_id=seller.id, phone=customer_phone).first()
        if customer:
            customer.total_purchases += total
            customer.visit_count += 1
            customer.last_visit = datetime.utcnow()
            if customer_whatsapp:
                customer.whatsapp = customer_whatsapp
        else:
            customer = Customer(
                seller_id=seller.id,
                name=customer_name,
                phone=customer_phone,
                whatsapp=customer_whatsapp,
                total_purchases=total,
                visit_count=1,
                last_visit=datetime.utcnow()
            )
            db.session.add(customer)
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'receipt_number': receipt_number,
        'total': total
    })

@app.route('/seller/pos/receipt/<receipt_number>')
@role_required('seller')
def pos_receipt(receipt_number):
    sale = OfflineSale.query.filter_by(receipt_number=receipt_number).first_or_404()
    
    if sale.seller_id != session['user_id']:
        flash('Access denied.', 'danger')
        return redirect(url_for('pos_dashboard'))
    
    return render_template('pos_receipt.html', sale=sale)

# ===== CUSTOMER MANAGEMENT =====

@app.route('/seller/customers')
@role_required('seller')
def customer_list():
    seller = User.query.get(session['user_id'])
    customers = Customer.query.filter_by(seller_id=seller.id).order_by(Customer.total_purchases.desc()).all()
    
    return render_template('customer_list.html', customers=customers)

@app.route('/seller/customer/<int:id>')
@role_required('seller')
def customer_detail(id):
    customer = Customer.query.get_or_404(id)
    
    if customer.seller_id != session['user_id']:
        flash('Access denied.', 'danger')
        return redirect(url_for('customer_list'))
    
    purchases = Sale.query.filter_by(seller_id=session['user_id'], customer_id=customer.id).order_by(Sale.sale_date.desc()).all()
    
    return render_template('customer_detail.html', customer=customer, purchases=purchases)

@app.route('/seller/customer/add', methods=['POST'])
@role_required('seller')
def add_customer():
    seller_id = session['user_id']
    name = request.form.get('name')
    phone = request.form.get('phone')
    whatsapp = request.form.get('whatsapp', phone)
    email = request.form.get('email')
    address = request.form.get('address')
    
    customer = Customer(
        seller_id=seller_id,
        name=name,
        phone=phone,
        whatsapp=whatsapp,
        email=email,
        address=address
    )
    
    db.session.add(customer)
    db.session.commit()
    
    flash('Customer added successfully!', 'success')
    return redirect(url_for('customer_list'))

# ===== SALES TRACKING & REPORTS =====

@app.route('/seller/sales')
@role_required('seller')
def sales_report():
    seller = User.query.get(session['user_id'])
    
    period = request.args.get('period', 'month')
    today = datetime.now().date()
    
    if period == 'day':
        start_date = today
        sales = Sale.query.filter(
            Sale.seller_id == seller.id,
            db.func.date(Sale.sale_date) == today
        ).all()
    elif period == 'week':
        start_date = today - timedelta(days=today.weekday())
        sales = Sale.query.filter(
            Sale.seller_id == seller.id,
            Sale.sale_date >= start_date
        ).all()
    elif period == 'month':
        start_date = today.replace(day=1)
        sales = Sale.query.filter(
            Sale.seller_id == seller.id,
            Sale.sale_date >= start_date
        ).all()
    else:
        start_date = today.replace(month=1, day=1)
        sales = Sale.query.filter(
            Sale.seller_id == seller.id,
            Sale.sale_date >= start_date
        ).all()
    
    total_sales = sum(s.total_price for s in sales)
    total_orders = len(sales)
    avg_order_value = total_sales / total_orders if total_orders > 0 else 0
    
    sales_by_product = db.session.query(
        Product.name, db.func.sum(Sale.total_price).label('total'), db.func.count(Sale.id).label('count')
    ).join(Sale, Sale.product_id == Product.id).filter(
        Sale.seller_id == seller.id,
        Sale.sale_date >= start_date
    ).group_by(Product.id).order_by(db.desc('total')).all()
    
    sales_by_day = db.session.query(
        db.func.date(Sale.sale_date).label('date'), db.func.sum(Sale.total_price).label('total')
    ).filter(
        Sale.seller_id == seller.id,
        Sale.sale_date >= start_date
    ).group_by(db.func.date(Sale.sale_date)).order_by('date').all()
    
    return render_template('sales_report.html',
                         period=period,
                         total_sales=total_sales,
                         total_orders=total_orders,
                         avg_order_value=avg_order_value,
                         sales_by_product=sales_by_product,
                         sales_by_day=sales_by_day)

# ===== CHECKOUT & ORDER PROCESSING =====

@app.route('/cart')
@login_required
def cart():
    cart_items = session.get('cart', {})
    products = []
    subtotal = 0
    
    for product_id, quantity in cart_items.items():
        product = Product.query.get(int(product_id))
        if product and product.is_active:
            products.append({'product': product, 'quantity': quantity})
            subtotal += product.price * quantity
    
    rider_fee = subtotal * 0.10
    platform_fee = subtotal * 0.10
    total = subtotal + rider_fee + platform_fee
    
    return render_template('cart.html', 
                         products=products, 
                         subtotal=subtotal,
                         rider_fee=rider_fee,
                         platform_fee=platform_fee,
                         total=total)

@app.route('/add_to_cart/<int:id>', methods=['POST'])
@login_required
def add_to_cart(id):
    quantity = int(request.form.get('quantity', 1))
    product = Product.query.get_or_404(id)
    
    if not product.is_active:
        flash('This product is no longer available.', 'warning')
        return redirect(url_for('products'))
    
    if product.stock < quantity:
        flash(f'Sorry, only {product.stock} units available.', 'warning')
        return redirect(url_for('product_detail', id=id))
    
    cart = session.get('cart', {})
    cart[str(id)] = cart.get(str(id), 0) + quantity
    session['cart'] = cart
    flash(f'Added {quantity} x {product.name} to cart!', 'success')
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
        delivery_lat = request.form.get('delivery_lat', type=float)
        delivery_lng = request.form.get('delivery_lng', type=float)
        payment_method = request.form.get('payment_method', 'cash')
        notes = request.form.get('notes', '')
        user = User.query.get(session['user_id'])
        
        subtotal = 0
        order_items = []
        sellers = set()
        
        for product_id, quantity in cart.items():
            product = Product.query.get(int(product_id))
            if product and product.is_active and product.stock >= quantity:
                item_total = product.price * quantity
                subtotal += item_total
                sellers.add(product.seller_id)
                order_items.append({
                    'product': product,
                    'quantity': quantity,
                    'price': product.price,
                    'seller_price': product.base_price
                })
        
        if not order_items:
            flash('Some items are no longer available.', 'warning')
            return redirect(url_for('cart'))
        
        rider_fee = subtotal * 0.10
        platform_fee = subtotal * 0.10
        total = subtotal + rider_fee + platform_fee
        
        order = Order(
            user_id=user.id,
            subtotal=subtotal,
            rider_fee=rider_fee,
            platform_fee=platform_fee,
            total=total,
            status='pending',
            delivery_address=delivery_address,
            delivery_lat=delivery_lat,
            delivery_lng=delivery_lng,
            payment_method=payment_method,
            notes=notes
        )
        
        if len(sellers) == 1:
            order.seller_id = list(sellers)[0]
        
        db.session.add(order)
        db.session.flush()
        
        for item in order_items:
            product = item['product']
            
            previous_stock = product.stock
            product.stock -= item['quantity']
            
            order_item = OrderItem(
                order_id=order.id,
                product_id=product.id,
                seller_id=product.seller_id,
                quantity=item['quantity'],
                price=item['price'],
                seller_price=item['seller_price']
            )
            db.session.add(order_item)
            
            log_inventory_change(product.id, product.seller_id, previous_stock, product.stock, 'order', order.id)
            
            sale = Sale(
                seller_id=product.seller_id,
                product_id=product.id,
                quantity=item['quantity'],
                unit_price=item['price'],
                total_price=item['price'] * item['quantity'],
                sale_type='online',
                status='pending'
            )
            db.session.add(sale)
        
        rider = find_nearest_rider(delivery_address)
        if rider:
            order.rider_id = rider.id
        
        db.session.commit()
        
        session['cart'] = {}
        
        flash('Order placed successfully!', 'success')
        return redirect(url_for('order_detail', id=order.id))
    
    cart = session.get('cart', {})
    products = []
    subtotal = 0
    
    for product_id, quantity in cart.items():
        product = Product.query.get(int(product_id))
        if product and product.is_active:
            products.append({'product': product, 'quantity': quantity})
            subtotal += product.price * quantity
    
    if not products:
        return redirect(url_for('cart'))
    
    rider_fee = subtotal * 0.10
    platform_fee = subtotal * 0.10
    total = subtotal + rider_fee + platform_fee
    
    return render_template('checkout.html', 
                         products=products,
                         subtotal=subtotal,
                         rider_fee=rider_fee,
                         platform_fee=platform_fee,
                         total=total)

@app.route('/order/<int:id>')
@login_required
def order_detail(id):
    order = Order.query.get_or_404(id)
    user = User.query.get(session['user_id'])
    
    if user.role != 'admin' and order.user_id != user.id and order.seller_id != user.id and order.rider_id != user.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    return render_template('order_detail.html', order=order)

@app.route('/orders')
@login_required
def orders():
    user = User.query.get(session['user_id'])
    
    if user.role == 'seller':
        orders = Order.query.filter_by(seller_id=user.id).order_by(Order.created_at.desc()).all()
    elif user.role == 'rider':
        orders = Order.query.filter_by(rider_id=user.id).order_by(Order.created_at.desc()).all()
    else:
        orders = Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).all()
    
    return render_template('orders.html', orders=orders)

# ===== RIDER FEATURES =====

@app.route('/rider/dashboard')
@role_required('rider')
def rider_dashboard():
    rider = User.query.get(session['user_id'])
    deliveries = Order.query.filter_by(rider_id=rider.id).order_by(Order.created_at.desc()).all()
    
    completed = sum(1 for o in deliveries if o.status == 'completed')
    pending = sum(1 for o in deliveries if o.status == 'pending')
    in_transit = sum(1 for o in deliveries if o.status == 'in_transit')
    total_earnings = sum(o.rider_fee for o in deliveries if o.status == 'completed')
    
    # Get available orders for assignment
    available_orders = Order.query.filter_by(rider_id=None, status='pending').all()
    
    return render_template('rider_dashboard.html', 
                         deliveries=deliveries,
                         available_orders=available_orders,
                         stats={'completed': completed, 'pending': pending, 'in_transit': in_transit, 'earnings': total_earnings})

@app.route('/rider/update_status/<int:id>', methods=['POST'])
@role_required('rider')
def update_delivery_status(id):
    order = Order.query.get_or_404(id)
    if order.rider_id != session['user_id']:
        flash('Access denied.', 'danger')
        return redirect(url_for('rider_dashboard'))
    
    status = request.form['status']
    order.status = status
    
    if status == 'completed':
        order.completed_at = datetime.utcnow()
        
        for item in order.items:
            sale = Sale.query.filter_by(seller_id=item.seller_id, product_id=item.product_id).filter(Sale.sale_date >= order.created_at).first()
            if sale:
                sale.status = 'completed'
    
    db.session.commit()
    
    flash('Delivery status updated!', 'success')
    return redirect(url_for('rider_dashboard'))

@app.route('/rider/accept_order/<int:id>', methods=['POST'])
@role_required('rider')
def accept_order(id):
    order = Order.query.get_or_404(id)
    
    if order.rider_id is not None:
        flash('Order already assigned to another rider.', 'danger')
        return redirect(url_for('rider_dashboard'))
    
    order.rider_id = session['user_id']
    order.status = 'in_transit'
    db.session.commit()
    
    flash('Order accepted!', 'success')
    return redirect(url_for('rider_dashboard'))

# ===== BUYER-SELLER CHAT =====

@app.route('/chat/<int:receiver_id>')
@login_required
def chat_with_user(receiver_id):
    receiver = User.query.get_or_404(receiver_id)
    product_id = request.args.get('product_id')
    product = Product.query.get(product_id) if product_id else None
    
    messages = Message.query.filter(
        ((Message.sender_id == session['user_id']) & (Message.receiver_id == receiver_id)) |
        ((Message.sender_id == receiver_id) & (Message.receiver_id == session['user_id']))
    ).order_by(Message.created_at).all()
    
    unread = Message.query.filter_by(receiver_id=session['user_id'], sender_id=receiver_id, is_read=False).all()
    for msg in unread:
        msg.is_read = True
    
    if unread:
        db.session.commit()
        total_unread = Message.query.filter_by(receiver_id=session['user_id'], is_read=False).count()
        session['unread_count'] = total_unread
    
    return render_template('chat.html', 
                         receiver=receiver, 
                         messages=messages,
                         product=product)

@app.route('/send_message', methods=['POST'])
@login_required
def send_message():
    receiver_id = request.form.get('receiver_id')
    message = request.form.get('message')
    product_id = request.form.get('product_id')
    
    if not receiver_id or not message:
        flash('Message cannot be empty.', 'danger')
        return redirect(request.referrer or url_for('index'))
    
    msg = Message(
        sender_id=session['user_id'],
        receiver_id=receiver_id,
        message=message,
        product_id=product_id if product_id else None
    )
    
    db.session.add(msg)
    db.session.commit()
    
    return redirect(url_for('chat_with_user', receiver_id=receiver_id, product_id=product_id))

@app.route('/inbox')
@login_required
def inbox():
    user_id = session['user_id']
    
    sent = db.session.query(Message.receiver_id).filter(Message.sender_id == user_id).distinct().subquery()
    received = db.session.query(Message.sender_id).filter(Message.receiver_id == user_id).distinct().subquery()
    
    conversation_partner_ids = db.session.query(sent.c.receiver_id).union(db.session.query(received.c.sender_id)).distinct().all()
    conversation_partner_ids = [id[0] for id in conversation_partner_ids]
    
    conversations = []
    for partner_id in conversation_partner_ids:
        partner = User.query.get(partner_id)
        last_message = Message.query.filter(
            ((Message.sender_id == user_id) & (Message.receiver_id == partner_id)) |
            ((Message.sender_id == partner_id) & (Message.receiver_id == user_id))
        ).order_by(Message.created_at.desc()).first()
        
        unread_count = Message.query.filter_by(sender_id=partner_id, receiver_id=user_id, is_read=False).count()
        
        conversations.append({
            'partner': partner,
            'last_message': last_message,
            'unread_count': unread_count
        })
    
    conversations.sort(key=lambda x: x['last_message'].created_at if x['last_message'] else datetime.min, reverse=True)
    
    return render_template('inbox.html', conversations=conversations)

# ===== ENHANCED COMMUNITY FEATURES =====

@app.route('/community', methods=['GET', 'POST'])
@login_required
def community():
    if request.method == 'POST':
        content = request.form['content']
        title = request.form.get('title', '')
        post_type = request.form.get('post_type', 'general')
        
        if not content.strip():
            flash('Post content cannot be empty.', 'danger')
            return redirect(url_for('community'))
        
        post = Post(
            user_id=session['user_id'],
            content=content.strip(),
            title=title.strip() if title else None,
            post_type=post_type
        )
        
        try:
            db.session.add(post)
            db.session.commit()
            flash('Post added to community!', 'success')
        except Exception as e:
            db.session.rollback()
            flash('Error creating post. Please try again.', 'danger')
            
        return redirect(url_for('community'))
    
    filter_type = request.args.get('filter', 'all')
    page = request.args.get('page', 1, type=int)
    per_page = 10
    
    query = Post.query
    
    if filter_type != 'all':
        query = query.filter_by(post_type=filter_type)
    
    posts = query.order_by(Post.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    
    users = User.query.filter(User.id != session['user_id']).limit(10).all()
    post_types = ['general', 'question', 'suggestion', 'feedback']
    recent_posts = Post.query.order_by(Post.created_at.desc()).limit(5).all()
    
    return render_template('community.html', 
                         posts=posts.items,
                         pagination=posts,
                         users=users,
                         post_types=post_types,
                         current_filter=filter_type,
                         recent_posts=recent_posts)

@app.route('/post/<int:id>', methods=['GET', 'POST'])
@login_required
def view_post(id):
    post = Post.query.get_or_404(id)
    
    if request.method == 'POST':
        content = request.form['content']
        
        if not content.strip():
            flash('Comment cannot be empty.', 'danger')
            return redirect(url_for('view_post', id=post.id))
        
        comment = Comment(
            post_id=post.id,
            user_id=session['user_id'],
            content=content.strip()
        )
        
        try:
            db.session.add(comment)
            db.session.commit()
            flash('Comment added!', 'success')
        except Exception as e:
            db.session.rollback()
            flash('Error adding comment.', 'danger')
            
        return redirect(url_for('view_post', id=post.id))
    
    comments = Comment.query.filter_by(post_id=post.id)\
                           .order_by(Comment.created_at)\
                           .all()
    
    return render_template('view_post.html', post=post, comments=comments)

@app.route('/post/<int:id>/like')
@login_required
def like_post(id):
    post = Post.query.get_or_404(id)
    post.likes += 1
    
    try:
        db.session.commit()
        flash('Post liked!', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Error liking post.', 'danger')
    
    return redirect(request.referrer or url_for('community'))

@app.route('/post/<int:id>/delete', methods=['POST'])
@login_required
def delete_post(id):
    post = Post.query.get_or_404(id)
    
    if post.user_id != session['user_id'] and session['role'] != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    try:
        Comment.query.filter_by(post_id=id).delete()
        db.session.delete(post)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

# ===== REVIEW SYSTEM =====

@app.route('/review/<int:user_id>', methods=['GET', 'POST'])
@login_required
def review_user(user_id):
    reviewed_user = User.query.get_or_404(user_id)
    order_id = request.args.get('order_id')
    
    existing_review = Review.query.filter_by(reviewer_id=session['user_id'], reviewed_id=user_id).first()
    if existing_review and not order_id:
        flash('You have already reviewed this user.', 'warning')
        return redirect(url_for('community'))
    
    if request.method == 'POST':
        review = Review(
            reviewer_id=session['user_id'],
            reviewed_id=user_id,
            rating=int(request.form['rating']),
            comment=request.form['comment'],
            order_id=order_id if order_id else None
        )
        db.session.add(review)
        db.session.commit()
        
        flash('Review submitted successfully!', 'success')
        return redirect(url_for('community'))
    
    return render_template('review.html', user=reviewed_user, order_id=order_id)

# ===== AI CHAT ASSISTANT =====

@app.route('/ai-assistant', methods=['GET', 'POST'])
@login_required
def ai_assistant():
    response_text = None
    user_message = ""
    suggestions = []
    
    if request.method == 'POST':
        user_message = request.form['message']
        
        if co:
            try:
                user = User.query.get(session['user_id'])
                
                recent_orders = Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).limit(3).all()
                
                context = f"The user is a {user.role} named {user.username}. "
                if recent_orders:
                    order_list = []
                    for order in recent_orders:
                        items = [f"{item.quantity}x {item.product.name}" for item in order.items]
                        order_list.append(f"Order #{order.id}: {', '.join(items)}")
                    context += f"They recently ordered: {'; '.join(order_list)}. "
                
                product_count = Product.query.filter_by(is_active=True).count()
                seller_count = User.query.filter_by(role='seller').count()
                context += f"The marketplace has {product_count} products from {seller_count} sellers. "
                
                response = co.chat(
                    message=user_message,
                    model="command",
                    preamble=f"You are a helpful assistant for Fresh Marketplace, a food delivery platform. {context}",
                    temperature=0.7
                )
                
                if hasattr(response, 'text'):
                    response_text = response.text
                elif isinstance(response, dict) and 'text' in response:
                    response_text = response['text']
                else:
                    response_text = str(response)
                    
            except ImportError:
                response_text = "Cohere library not properly configured."
            except Exception as e:
                response_text = f"AI service temporarily unavailable. Please try again later."
                print(f"AI Error: {str(e)}")
        else:
            response_text = "AI assistant is currently unavailable. Please contact support if this persists."
    
    suggestions = [
        "What are today's specials?",
        "How do I track my order?",
        "What payment methods do you accept?",
        "How do I become a seller?",
        "Tell me about your delivery policy",
        "Do you have any discounts?"
    ]
    
    return render_template('ai_assistant.html', 
                         response=response_text, 
                         user_message=user_message,
                         suggestions=suggestions)

# ===== NOTIFICATION API =====

@app.route('/api/notifications')
@login_required
def get_notifications():
    user_id = session['user_id']
    
    unread_count = Message.query.filter_by(receiver_id=user_id, is_read=False).count()
    
    latest_message = Message.query.filter_by(receiver_id=user_id, is_read=False)\
                                 .order_by(Message.created_at.desc()).first()
    
    return jsonify({
        'unread_count': unread_count,
        'latest_message': latest_message.message if latest_message else None,
        'sender': latest_message.sender.username if latest_message else None
    })

# ===== ADMIN DASHBOARD =====

@app.route('/admin/dashboard')
@role_required('admin')
def admin_dashboard():
    users = User.query.all()
    products = Product.query.all()
    orders = Order.query.order_by(Order.created_at.desc()).limit(50).all()
    
    total_users = len(users)
    total_sellers = len([u for u in users if u.role == 'seller'])
    total_riders = len([u for u in users if u.role == 'rider'])
    total_buyers = len([u for u in users if u.role == 'buyer'])
    
    total_products = len(products)
    total_orders = Order.query.count()
    pending_orders = Order.query.filter_by(status='pending').count()
    completed_orders = Order.query.filter_by(status='completed').count()
    
    total_revenue = db.session.query(db.func.sum(Order.total)).scalar() or 0
    total_platform_fees = db.session.query(db.func.sum(Order.platform_fee)).scalar() or 0
    total_rider_fees = db.session.query(db.func.sum(Order.rider_fee)).scalar() or 0
    
    active_calls = VideoCall.query.filter_by(status='active').count()
    
    stats = {
        'total_users': total_users,
        'total_sellers': total_sellers,
        'total_riders': total_riders,
        'total_buyers': total_buyers,
        'total_products': total_products,
        'total_orders': total_orders,
        'pending_orders': pending_orders,
        'completed_orders': completed_orders,
        'total_revenue': total_revenue,
        'total_platform_fees': total_platform_fees,
        'total_rider_fees': total_rider_fees,
        'active_calls': active_calls
    }
    
    return render_template('admin_dashboard.html', 
                         stats=stats, 
                         users=users[:10], 
                         products=products[:10], 
                         orders=orders)

# ===== SOCKETIO EVENTS =====

@socketio.on('connect')
def handle_connect():
    if 'user_id' in session:
        user_id = session['user_id']
        join_room(f'user_{user_id}')
        
        user = User.query.get(user_id)
        if user:
            user.is_online = True
            user.last_seen = datetime.utcnow()
            db.session.commit()

@socketio.on('disconnect')
def handle_disconnect():
    if 'user_id' in session:
        user_id = session['user_id']
        leave_room(f'user_{user_id}')
        
        user = User.query.get(user_id)
        if user:
            user.is_online = False
            user.last_seen = datetime.utcnow()
            db.session.commit()

@socketio.on('join_call')
def handle_join_call(data):
    call_id = data.get('call_id')
    room = f'call_{call_id}'
    join_room(room)
    
    emit('user_joined', {
        'user_id': session['user_id'],
        'username': session['username']
    }, room=room)

@socketio.on('leave_call')
def handle_leave_call(data):
    call_id = data.get('call_id')
    room = f'call_{call_id}'
    leave_room(room)
    
    emit('user_left', {
        'user_id': session['user_id'],
        'username': session['username']
    }, room=room)

@socketio.on('track_order')
def handle_track_order(data):
    order_id = data.get('order_id')
    room = f'order_{order_id}'
    join_room(room)

# ===== DATABASE INITIALIZATION =====

def init_db():
    with app.app_context():
        db.create_all()
        
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin',
                email='admin@marketplace.com',
                password=generate_password_hash('admin123'),
                role='admin',
                phone_number='1234567890',
                whatsapp_number='1234567890',
                location='Main Office',
                profile_image='/static/images/default-profile.png'
            )
            db.session.add(admin)
            
            seller = User(
                username='seller1',
                email='seller@marketplace.com',
                password=generate_password_hash('seller123'),
                role='seller',
                phone_number='5551234567',
                whatsapp_number='5551234567',
                location='Market Street',
                business_name='Fresh Farm Produce',
                business_address='123 Farm Road',
                profile_image='/static/images/default-profile.png'
            )
            db.session.add(seller)
            
            rider = User(
                username='rider1',
                email='rider@marketplace.com',
                password=generate_password_hash('rider123'),
                role='rider',
                phone_number='0987654321',
                whatsapp_number='0987654321',
                location='Downtown',
                profile_image='/static/images/default-profile.png'
            )
            db.session.add(rider)
            
            db.session.flush()
            
            products = [
                Product(name='Fresh Apples', description='Crisp and sweet red apples', 
                       base_price=3.99, category='Fruits', 
                       image_url='https://images.unsplash.com/photo-1560806887-1e4cd0b6cbd6?w=400', 
                       stock=50, seller_id=seller.id, sku='FRUIT-001', video_call_enabled=True),
                Product(name='Organic Bananas', description='Yellow ripe bananas', 
                       base_price=2.49, category='Fruits',
                       image_url='https://images.unsplash.com/photo-1603833665858-e61d17a86224?w=400', 
                       stock=100, seller_id=seller.id, sku='FRUIT-002', video_call_enabled=True),
                Product(name='Fresh Tomatoes', description='Ripe red tomatoes', 
                       base_price=4.99, category='Vegetables',
                       image_url='https://images.unsplash.com/photo-1546094096-0df4bcaaa337?w=400', 
                       stock=75, seller_id=seller.id, sku='VEG-001', video_call_enabled=True),
            ]
            
            for product in products:
                final_price, rider_fee, platform_fee = calculate_price_with_fees(product.base_price)
                product.price = final_price
                db.session.add(product)
            
            db.session.commit()
            print('Database initialized with sample data!')

with app.app_context():
    try:
        db.create_all()
        print("✅ Database tables created/verified successfully!")
        
        create_superuser()
        
        if not User.query.filter_by(username='admin').first():
            print("📦 Adding sample data...")
            init_db()
            print("✅ Sample data added successfully!")
        else:
            print("ℹ️ Database already has data, skipping sample data")
            
    except Exception as e:
        print(f"⚠️ Database initialization error: {e}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=os.environ.get('FLASK_ENV') == 'development', allow_unsafe_werkzeug=True)
