from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, abort
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import os
from functools import wraps
import cohere
import uuid
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# Create upload folders
os.makedirs(os.path.join(app.root_path, UPLOAD_FOLDER), exist_ok=True)
os.makedirs(os.path.join(app.root_path, 'static/images'), exist_ok=True)

# Create default profile image
default_profile_path = os.path.join(app.root_path, 'static/images/default-profile.png')
if not os.path.exists(default_profile_path):
    with open(default_profile_path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0bIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\x0d\n\x08\xb4\x00\x00\x00\x00IEND\xaeB`\x82')

db = SQLAlchemy(app)

# Initialize Cohere
cohere_api_key = os.environ.get('COHERE_API_KEY')
co = cohere.Client(cohere_api_key) if cohere_api_key else None

# ===== FIXED MODELS WITH PROPER RELATIONSHIPS =====
class User(db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    phone_number = db.Column(db.String(20), nullable=False)
    whatsapp_number = db.Column(db.String(20))
    location = db.Column(db.String(200))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    profile_image = db.Column(db.String(500), default='/static/images/default-profile.png')
    business_name = db.Column(db.String(200))
    business_address = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    is_online = db.Column(db.Boolean, default=False)
    
    # Relationships - ALL with explicit foreign_keys
    products = db.relationship('Product', back_populates='seller', lazy=True)
    sales = db.relationship('Sale', back_populates='seller', foreign_keys='Sale.seller_id', lazy=True)
    offline_sales = db.relationship('OfflineSale', back_populates='seller', lazy=True)
    customers_as_seller = db.relationship('Customer', foreign_keys='Customer.seller_id', back_populates='seller', lazy=True)
    customers_as_user = db.relationship('Customer', foreign_keys='Customer.user_id', back_populates='user', lazy=True)
    locations = db.relationship('UserLocation', back_populates='user', lazy=True)
    sent_messages = db.relationship('Message', foreign_keys='Message.sender_id', back_populates='sender', lazy=True)
    received_messages = db.relationship('Message', foreign_keys='Message.receiver_id', back_populates='receiver', lazy=True)
    posts = db.relationship('Post', back_populates='author', lazy=True)
    reviews_given = db.relationship('Review', foreign_keys='Review.reviewer_id', back_populates='reviewer', lazy=True)
    reviews_received = db.relationship('Review', foreign_keys='Review.reviewed_id', back_populates='reviewed', lazy=True)
    initiated_calls = db.relationship('VideoCall', foreign_keys='VideoCall.initiator_id', back_populates='initiator', lazy=True)
    received_calls = db.relationship('VideoCall', foreign_keys='VideoCall.receiver_id', back_populates='receiver', lazy=True)
    inventory_logs = db.relationship('InventoryLog', back_populates='seller', lazy=True)
    product_inquiries_as_buyer = db.relationship('ProductInquiry', foreign_keys='ProductInquiry.buyer_id', back_populates='buyer', lazy=True)
    product_inquiries_as_seller = db.relationship('ProductInquiry', foreign_keys='ProductInquiry.seller_id', back_populates='seller', lazy=True)
    rider_recommendations_as_buyer = db.relationship('RiderRecommendation', foreign_keys='RiderRecommendation.buyer_id', back_populates='buyer', lazy=True)
    rider_recommendations_as_seller = db.relationship('RiderRecommendation', foreign_keys='RiderRecommendation.seller_id', back_populates='seller', lazy=True)
    notifications = db.relationship('Notification', back_populates='user', lazy=True)

class Product(db.Model):
    __tablename__ = 'product'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    base_price = db.Column(db.Float, nullable=False)
    price = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50))
    image_url = db.Column(db.String(500))
    additional_images = db.Column(db.JSON, default=list)
    stock = db.Column(db.Integer, default=0)
    reserved_stock = db.Column(db.Integer, default=0)
    sku = db.Column(db.String(50), unique=True)
    barcode = db.Column(db.String(100))
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    low_stock_threshold = db.Column(db.Integer, default=5)
    video_call_enabled = db.Column(db.Boolean, default=True)
    virtual_tour_url = db.Column(db.String(500))
    
    seller = db.relationship('User', back_populates='products')
    order_items = db.relationship('OrderItem', back_populates='product', lazy=True)
    reviews = db.relationship('Review', back_populates='product', lazy=True)
    inventory_logs = db.relationship('InventoryLog', back_populates='product', lazy=True)
    inquiries = db.relationship('ProductInquiry', back_populates='product', lazy=True)

class Order(db.Model):
    __tablename__ = 'order'
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(20), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    rider_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    total = db.Column(db.Float, nullable=False)
    subtotal = db.Column(db.Float, nullable=False, default=0)
    rider_fee = db.Column(db.Float, default=0)
    platform_fee = db.Column(db.Float, default=0)
    status = db.Column(db.String(20), default='pending')
    delivery_address = db.Column(db.String(200))
    delivery_lat = db.Column(db.Float)
    delivery_lng = db.Column(db.Float)
    payment_method = db.Column(db.String(50), default='cash')
    payment_status = db.Column(db.String(20), default='pending')
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    confirmed_at = db.Column(db.DateTime)
    shipped_at = db.Column(db.DateTime)
    delivered_at = db.Column(db.DateTime)
    cancelled_at = db.Column(db.DateTime)
    
    user = db.relationship('User', foreign_keys=[user_id], backref='customer_orders')
    rider = db.relationship('User', foreign_keys=[rider_id], backref='rider_deliveries')
    seller = db.relationship('User', foreign_keys=[seller_id], backref='seller_orders')
    items = db.relationship('OrderItem', back_populates='order', lazy=True)
    tracking = db.relationship('OrderTracking', back_populates='order', lazy=True)
    reviews = db.relationship('Review', back_populates='order', lazy=True)
    notifications = db.relationship('Notification', back_populates='order', lazy=True)

class OrderItem(db.Model):
    __tablename__ = 'order_item'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    seller_price = db.Column(db.Float, nullable=False)
    
    order = db.relationship('Order', back_populates='items')
    product = db.relationship('Product', back_populates='order_items')
    seller = db.relationship('User', foreign_keys=[seller_id])

class Sale(db.Model):
    __tablename__ = 'sale'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'))
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
    
    order = db.relationship('Order')
    seller = db.relationship('User', back_populates='sales')
    product = db.relationship('Product')
    customer = db.relationship('Customer', back_populates='purchases')

class Customer(db.Model):
    __tablename__ = 'customer'
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    whatsapp = db.Column(db.String(20))
    email = db.Column(db.String(120))
    address = db.Column(db.String(200))
    total_purchases = db.Column(db.Float, default=0)
    purchase_count = db.Column(db.Integer, default=0)
    last_purchase = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # FIXED: Explicit foreign keys in relationships
    seller = db.relationship('User', foreign_keys=[seller_id], back_populates='customers_as_seller')
    user = db.relationship('User', foreign_keys=[user_id], back_populates='customers_as_user')
    purchases = db.relationship('Sale', back_populates='customer', lazy=True)

class Notification(db.Model):
    __tablename__ = 'notification'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'))
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    type = db.Column(db.String(50), default='info')
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', back_populates='notifications')
    order = db.relationship('Order', back_populates='notifications')

class Review(db.Model):
    __tablename__ = 'review'
    id = db.Column(db.Integer, primary_key=True)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reviewed_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'))
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    reviewer = db.relationship('User', foreign_keys=[reviewer_id], back_populates='reviews_given')
    reviewed = db.relationship('User', foreign_keys=[reviewed_id], back_populates='reviews_received')
    product = db.relationship('Product', back_populates='reviews')
    order = db.relationship('Order', back_populates='reviews')

class Post(db.Model):
    __tablename__ = 'post'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    title = db.Column(db.String(200))
    post_type = db.Column(db.String(20), default='general')
    likes = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    author = db.relationship('User', back_populates='posts')
    comments = db.relationship('Comment', back_populates='post', lazy=True)

class Comment(db.Model):
    __tablename__ = 'comment'
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    post = db.relationship('Post', back_populates='comments')
    user = db.relationship('User')

class Message(db.Model):
    __tablename__ = 'message'
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    sender = db.relationship('User', foreign_keys=[sender_id], back_populates='sent_messages')
    receiver = db.relationship('User', foreign_keys=[receiver_id], back_populates='received_messages')
    product = db.relationship('Product')

class UserLocation(db.Model):
    __tablename__ = 'user_location'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    accuracy = db.Column(db.Float)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', back_populates='locations')

class OrderTracking(db.Model):
    __tablename__ = 'order_tracking'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    rider_lat = db.Column(db.Float)
    rider_lng = db.Column(db.Float)
    status = db.Column(db.String(20))
    message = db.Column(db.String(200))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    order = db.relationship('Order', back_populates='tracking')

class VideoCall(db.Model):
    __tablename__ = 'video_call'
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.String(100), unique=True, nullable=False)
    initiator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    status = db.Column(db.String(20), default='pending')
    started_at = db.Column(db.DateTime)
    ended_at = db.Column(db.DateTime)
    ai_assistant_active = db.Column(db.Boolean, default=False)
    recording_enabled = db.Column(db.Boolean, default=False)
    
    initiator = db.relationship('User', foreign_keys=[initiator_id], back_populates='initiated_calls')
    receiver = db.relationship('User', foreign_keys=[receiver_id], back_populates='received_calls')
    product = db.relationship('Product')
    messages = db.relationship('VideoCallMessage', back_populates='call', lazy=True)

class VideoCallMessage(db.Model):
    __tablename__ = 'video_call_message'
    id = db.Column(db.Integer, primary_key=True)
    call_id = db.Column(db.Integer, db.ForeignKey('video_call.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.Text)
    is_ai = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    call = db.relationship('VideoCall', back_populates='messages')
    sender = db.relationship('User')

class InventoryLog(db.Model):
    __tablename__ = 'inventory_log'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    previous_stock = db.Column(db.Integer)
    new_stock = db.Column(db.Integer)
    change = db.Column(db.Integer)
    reason = db.Column(db.String(100))
    reference_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    product = db.relationship('Product', back_populates='inventory_logs')
    seller = db.relationship('User', back_populates='inventory_logs')

class ProductInquiry(db.Model):
    __tablename__ = 'product_inquiry'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    buyer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text)
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    answered_at = db.Column(db.DateTime)
    
    product = db.relationship('Product', back_populates='inquiries')
    buyer = db.relationship('User', foreign_keys=[buyer_id], back_populates='product_inquiries_as_buyer')
    seller = db.relationship('User', foreign_keys=[seller_id], back_populates='product_inquiries_as_seller')
    notifications = db.relationship('Notification', backref='inquiry', lazy=True)

class RiderRecommendation(db.Model):
    __tablename__ = 'rider_recommendation'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'))
    buyer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    recommended_rider_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reason = db.Column(db.Text)
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    buyer = db.relationship('User', foreign_keys=[buyer_id], back_populates='rider_recommendations_as_buyer')
    seller = db.relationship('User', foreign_keys=[seller_id], back_populates='rider_recommendations_as_seller')
    recommended_rider = db.relationship('User', foreign_keys=[recommended_rider_id])

class OfflineSale(db.Model):
    __tablename__ = 'offline_sale'
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
    
    seller = db.relationship('User', back_populates='offline_sales')

# ===== HELPER FUNCTIONS =====

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
                flash('Please log in to access this page.', 'warning')
                return redirect(url_for('login'))
            
            try:
                user = User.query.get(session['user_id'])
                if not user:
                    session.clear()
                    flash('User not found. Please log in again.', 'danger')
                    return redirect(url_for('login'))
                
                if user.role != role and user.role != 'admin':
                    flash('Access denied. You do not have permission to view this page.', 'danger')
                    return redirect(url_for('index'))
                    
                return f(*args, **kwargs)
            except Exception as e:
                logger.error(f"Role check error: {e}")
                flash('An error occurred. Please try again.', 'danger')
                return redirect(url_for('index'))
                
        return decorated_function
    return decorator

def calculate_price_with_fees(base_price):
    rider_fee = round(base_price * 0.10, 2)
    platform_fee = round(base_price * 0.10, 2)
    final_price = round(base_price + rider_fee + platform_fee, 2)
    return final_price, rider_fee, platform_fee

def find_nearest_rider(location):
    try:
        riders = User.query.filter_by(role='rider', is_online=True).all()
        return riders[0] if riders else None
    except:
        return None

def log_inventory_change(product_id, seller_id, previous_stock, new_stock, reason, reference_id=None):
    try:
        log = InventoryLog(
            product_id=product_id,
            seller_id=seller_id,
            previous_stock=previous_stock,
            new_stock=new_stock,
            change=new_stock - previous_stock,
            reason=reason,
            reference_id=reference_id
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        logger.error(f"Inventory log error: {e}")
        db.session.rollback()

def generate_order_number():
    date_part = datetime.now().strftime('%Y%m%d')
    random_part = uuid.uuid4().hex[:6].upper()
    return f"ORD-{date_part}-{random_part}"

def create_notification(user_id, title, message, type='info', order_id=None):
    try:
        notification = Notification(
            user_id=user_id,
            title=title,
            message=message,
            type=type,
            order_id=order_id
        )
        db.session.add(notification)
        db.session.commit()
        
        socketio.emit('notification', {
            'id': notification.id,
            'title': title,
            'message': message,
            'type': type,
            'order_id': order_id,
            'created_at': notification.created_at.isoformat()
        }, room=f'user_{user_id}')
        
        return notification
    except Exception as e:
        logger.error(f"Error creating notification: {e}")
        return None

def generate_receipt_number():
    return f"REC-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

def generate_video_room_id():
    return f"room-{uuid.uuid4().hex[:12]}"

# ===== ERROR HANDLERS =====

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    logger.error(f"Internal Server Error: {error}")
    return render_template('500.html'), 500

@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

# ===== CONTEXT PROCESSORS =====

@app.context_processor
def inject_available_riders():
    """Inject available riders and unread notifications for templates"""
    result = {
        'available_riders': [],
        'unread_notifications': 0
    }
    
    if 'user_id' in session:
        try:
            result['available_riders'] = User.query.filter_by(role='rider', is_online=True).limit(5).all()
        except Exception as e:
            logger.error(f"Error fetching riders: {e}")
            result['available_riders'] = []
        
        try:
            result['unread_notifications'] = Notification.query.filter_by(
                user_id=session['user_id'], 
                is_read=False
            ).count()
        except Exception as e:
            logger.error(f"Error fetching notifications: {e}")
            result['unread_notifications'] = 0
    
    return result

# ===== AUTHENTICATION ROUTES =====

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
            logger.error(f"Registration error: {e}")
            flash(f'Registration failed. Please try again.', 'danger')
            return redirect(url_for('register'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        remember = request.form.get('remember') == 'on'
        
        try:
            user = User.query.filter_by(username=username).first()
            
            if user and check_password_hash(user.password, password):
                session.permanent = remember
                session['user_id'] = user.id
                session['username'] = user.username
                session['role'] = user.role
                session['profile_image'] = user.profile_image
                
                user.is_online = True
                user.last_seen = datetime.utcnow()
                db.session.commit()
                
                try:
                    unread_count = Message.query.filter_by(receiver_id=user.id, is_read=False).count()
                    session['unread_count'] = unread_count
                except:
                    session['unread_count'] = 0
                
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
        except Exception as e:
            logger.error(f"Login error: {e}")
            flash('An error occurred during login. Please try again.', 'danger')
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

# ===== PUBLIC ROUTES =====

@app.route('/')
def index():
    try:
        products = Product.query.filter_by(is_active=True).order_by(Product.created_at.desc()).limit(8).all()
        return render_template('index.html', products=products)
    except Exception as e:
        logger.error(f"Index error: {e}")
        return render_template('index.html', products=[])

@app.route('/products')
def products():
    try:
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
        
        products = query.order_by(Product.created_at.desc()).all()
        categories = db.session.query(Product.category).distinct().all()
        category_list = [c[0] for c in categories if c[0]]
        
        return render_template('products.html', products=products, categories=category_list)
    except Exception as e:
        logger.error(f"Products error: {e}")
        return render_template('products.html', products=[], categories=[])

@app.route('/product/<int:id>')
def product_detail(id):
    try:
        product = Product.query.get(id)
        
        if not product:
            flash('Product not found.', 'danger')
            return redirect(url_for('products'))
        
        if not product.is_active:
            flash('This product is no longer available.', 'warning')
            return redirect(url_for('products'))
        
        seller = User.query.get(product.seller_id)
        related_products = Product.query.filter_by(category=product.category, is_active=True).filter(Product.id != product.id).limit(4).all()
        
        # Get product inquiries for this user if logged in
        product_inquiries = []
        if 'user_id' in session:
            product_inquiries = ProductInquiry.query.filter_by(product_id=id, buyer_id=session['user_id']).all()
        
        return render_template('product_detail.html', 
                             product=product, 
                             seller=seller, 
                             related_products=related_products,
                             product_inquiries=product_inquiries)
    except Exception as e:
        logger.error(f"Product detail error: {e}")
        flash('Error loading product. Please try again.', 'danger')
        return redirect(url_for('products'))

# ===== PRODUCT INQUIRY ROUTES =====

@app.route('/product/<int:id>/inquiry', methods=['POST'])
@login_required
def product_inquiry(id):
    product = Product.query.get_or_404(id)
    question = request.form.get('question')
    
    if not question:
        flash('Question cannot be empty.', 'danger')
        return redirect(url_for('product_detail', id=id))
    
    inquiry = ProductInquiry(
        product_id=id,
        buyer_id=session['user_id'],
        seller_id=product.seller_id,
        question=question
    )
    
    try:
        db.session.add(inquiry)
        db.session.commit()
        
        # Create notification for seller
        buyer = User.query.get(session['user_id'])
        create_notification(
            user_id=product.seller_id,
            title=f"New Question about {product.name}",
            message=f"{buyer.username} asked: {question[:100]}...",
            type='inquiry'
        )
        
        flash('Your question has been sent to the seller!', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Inquiry error: {e}")
        flash('Error sending question. Please try again.', 'danger')
    
    return redirect(url_for('product_detail', id=id))

@app.route('/product/<int:id>/recommend-rider', methods=['POST'])
@login_required
def recommend_rider(id):
    product = Product.query.get_or_404(id)
    rider_id = request.form.get('rider_id')
    reason = request.form.get('reason', '')
    
    if not rider_id:
        flash('Please select a rider.', 'danger')
        return redirect(url_for('product_detail', id=id))
    
    rider = User.query.get(rider_id)
    if not rider or rider.role != 'rider':
        flash('Invalid rider selected.', 'danger')
        return redirect(url_for('product_detail', id=id))
    
    recommendation = RiderRecommendation(
        buyer_id=session['user_id'],
        seller_id=product.seller_id,
        recommended_rider_id=rider_id,
        reason=reason
    )
    
    try:
        db.session.add(recommendation)
        db.session.commit()
        
        # Create notification for seller
        buyer = User.query.get(session['user_id'])
        create_notification(
            user_id=product.seller_id,
            title=f"Rider Recommendation for {product.name}",
            message=f"{buyer.username} recommended rider {rider.username}",
            type='recommendation'
        )
        
        flash(f'Rider {rider.username} recommended to seller!', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Recommendation error: {e}")
        flash('Error sending recommendation. Please try again.', 'danger')
    
    return redirect(url_for('product_detail', id=id))

@app.route('/seller/inquiries')
@role_required('seller')
def seller_inquiries():
    seller_id = session['user_id']
    inquiries = ProductInquiry.query.filter_by(seller_id=seller_id).order_by(ProductInquiry.created_at.desc()).all()
    return render_template('seller_inquiries.html', inquiries=inquiries)

@app.route('/inquiry/<int:id>/answer', methods=['POST'])
@login_required
def answer_inquiry(id):
    inquiry = ProductInquiry.query.get_or_404(id)
    
    if inquiry.seller_id != session['user_id']:
        flash('Access denied.', 'danger')
        return redirect(url_for('seller_inquiries'))
    
    answer = request.form.get('answer')
    if not answer:
        flash('Answer cannot be empty.', 'danger')
        return redirect(url_for('seller_inquiries'))
    
    inquiry.answer = answer
    inquiry.status = 'answered'
    inquiry.answered_at = datetime.utcnow()
    
    try:
        db.session.commit()
        
        # Create notification for buyer
        create_notification(
            user_id=inquiry.buyer_id,
            title=f"Your Question about {inquiry.product.name} has been Answered",
            message=f"Seller replied: {answer[:100]}...",
            type='inquiry'
        )
        
        flash('Answer sent to buyer!', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Error sending answer.', 'danger')
    
    return redirect(url_for('seller_inquiries'))

# ===== CART & CHECKOUT ROUTES =====

@app.route('/cart')
@login_required
def cart():
    try:
        cart_items = session.get('cart', {})
        products = []
        subtotal = 0
        
        for product_id, quantity in cart_items.items():
            product = Product.query.get(int(product_id))
            if product and product.is_active:
                available = product.stock - product.reserved_stock
                if quantity > available:
                    quantity = available
                    if quantity == 0:
                        continue
                
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
    except Exception as e:
        logger.error(f"Cart error: {e}")
        flash('Error loading cart.', 'danger')
        return redirect(url_for('index'))

@app.route('/add_to_cart/<int:id>', methods=['POST'])
@login_required
def add_to_cart(id):
    try:
        quantity = int(request.form.get('quantity', 1))
        product = Product.query.get(id)
        
        if not product:
            flash('Product not found.', 'danger')
            return redirect(url_for('products'))
        
        if not product.is_active:
            flash('This product is no longer available.', 'warning')
            return redirect(url_for('products'))
        
        available = product.stock - product.reserved_stock
        if available < quantity:
            flash(f'Sorry, only {available} units available.', 'warning')
            return redirect(url_for('product_detail', id=id))
        
        cart = session.get('cart', {})
        cart[str(id)] = cart.get(str(id), 0) + quantity
        session['cart'] = cart
        flash(f'Added {quantity} x {product.name} to cart!', 'success')
    except Exception as e:
        logger.error(f"Add to cart error: {e}")
        flash('Error adding to cart.', 'danger')
    
    return redirect(url_for('products'))

@app.route('/update_cart/<int:id>', methods=['POST'])
@login_required
def update_cart(id):
    try:
        quantity = int(request.form.get('quantity', 0))
        cart = session.get('cart', {})
        
        if quantity <= 0:
            if str(id) in cart:
                del cart[str(id)]
                flash('Item removed from cart.', 'success')
        else:
            product = Product.query.get(id)
            if product:
                available = product.stock - product.reserved_stock
                if quantity <= available:
                    cart[str(id)] = quantity
                    flash('Cart updated.', 'success')
                else:
                    flash(f'Only {available} units available.', 'danger')
        
        session['cart'] = cart
    except Exception as e:
        logger.error(f"Update cart error: {e}")
        flash('Error updating cart.', 'danger')
    
    return redirect(url_for('cart'))

@app.route('/remove_from_cart/<int:id>', methods=['POST'])
@login_required
def remove_from_cart(id):
    try:
        cart = session.get('cart', {})
        if str(id) in cart:
            del cart[str(id)]
            session['cart'] = cart
            flash('Item removed from cart.', 'success')
    except Exception as e:
        logger.error(f"Remove from cart error: {e}")
        flash('Error removing item.', 'danger')
    
    return redirect(url_for('cart'))

@app.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    if request.method == 'POST':
        try:
            cart = session.get('cart', {})
            if not cart:
                flash('Cart is empty.', 'warning')
                return redirect(url_for('products'))
            
            delivery_address = request.form['delivery_address']
            payment_method = request.form.get('payment_method', 'cash')
            user = User.query.get(session['user_id'])
            
            subtotal = 0
            order_items = []
            sellers = set()
            
            for product_id, quantity in cart.items():
                product = Product.query.get(int(product_id))
                if not product or not product.is_active:
                    flash(f'Product is no longer available.', 'warning')
                    return redirect(url_for('cart'))
                
                available = product.stock - product.reserved_stock
                if available < quantity:
                    flash(f'Only {available} units of {product.name} available.', 'warning')
                    return redirect(url_for('cart'))
                
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
                order_number=generate_order_number(),
                user_id=user.id,
                subtotal=subtotal,
                rider_fee=rider_fee,
                platform_fee=platform_fee,
                total=total,
                status='pending',
                delivery_address=delivery_address,
                payment_method=payment_method,
                payment_status='pending'
            )
            
            if len(sellers) == 1:
                order.seller_id = list(sellers)[0]
            
            db.session.add(order)
            db.session.flush()
            
            for item in order_items:
                product = item['product']
                product.reserved_stock += item['quantity']
                
                order_item = OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    seller_id=product.seller_id,
                    quantity=item['quantity'],
                    price=item['price'],
                    seller_price=item['seller_price']
                )
                db.session.add(order_item)
                
                log_inventory_change(
                    product.id, 
                    product.seller_id, 
                    product.stock - product.reserved_stock + item['quantity'], 
                    product.stock - product.reserved_stock, 
                    'reserve', 
                    order.id
                )
            
            rider = find_nearest_rider(delivery_address)
            if rider:
                order.rider_id = rider.id
            
            db.session.commit()
            
            session['cart'] = {}
            
            for seller_id in sellers:
                create_notification(
                    user_id=seller_id,
                    title=f"New Order #{order.order_number}",
                    message=f"You have received a new order worth Kes {total:.2f}",
                    type='order',
                    order_id=order.id
                )
            
            create_notification(
                user_id=user.id,
                title=f"Order #{order.order_number} Placed Successfully",
                message=f"Your order of Kes {total:.2f} has been placed and is pending confirmation.",
                type='order',
                order_id=order.id
            )
            
            flash('Order placed successfully!', 'success')
            return redirect(url_for('order_detail', id=order.id))
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Checkout error: {e}")
            flash('Error placing order. Please try again.', 'danger')
            return redirect(url_for('cart'))
    
    # GET request
    try:
        cart = session.get('cart', {})
        products = []
        subtotal = 0
        
        for product_id, quantity in cart.items():
            product = Product.query.get(int(product_id))
            if product and product.is_active:
                available = product.stock - product.reserved_stock
                if quantity > available:
                    quantity = available
                    if quantity == 0:
                        continue
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
    except Exception as e:
        logger.error(f"Checkout form error: {e}")
        flash('Error loading checkout.', 'danger')
        return redirect(url_for('cart'))

@app.route('/order/<int:id>')
@login_required
def order_detail(id):
    try:
        order = Order.query.get_or_404(id)
        user = User.query.get(session['user_id'])
        
        if user.role != 'admin' and order.user_id != user.id and order.seller_id != user.id and order.rider_id != user.id:
            flash('Access denied.', 'danger')
            return redirect(url_for('index'))
        
        return render_template('order_detail.html', order=order)
    except Exception as e:
        logger.error(f"Order detail error: {e}")
        flash('Order not found.', 'danger')
        return redirect(url_for('orders'))

@app.route('/orders')
@login_required
def orders():
    try:
        user = User.query.get(session['user_id'])
        
        if not user:
            flash('User not found.', 'danger')
            return redirect(url_for('login'))
        
        if user.role == 'seller':
            orders = Order.query.filter_by(seller_id=user.id).order_by(Order.created_at.desc()).all()
        elif user.role == 'rider':
            orders = Order.query.filter_by(rider_id=user.id).order_by(Order.created_at.desc()).all()
        else:
            orders = Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).all()
        
        return render_template('orders.html', orders=orders)
    except Exception as e:
        logger.error(f"Orders error: {e}")
        flash('Error loading orders. Please try again.', 'danger')
        return redirect(url_for('index'))

@app.route('/order/<int:id>/confirm', methods=['POST'])
@role_required('seller')
def confirm_order(id):
    try:
        order = Order.query.get_or_404(id)
        
        if order.seller_id != session['user_id']:
            flash('Access denied.', 'danger')
            return redirect(url_for('seller_dashboard'))
        
        if order.status != 'pending':
            flash('Order cannot be confirmed.', 'danger')
            return redirect(url_for('order_detail', id=id))
        
        order.status = 'confirmed'
        order.confirmed_at = datetime.utcnow()
        db.session.commit()
        
        create_notification(
            user_id=order.user_id,
            title=f"Order #{order.order_number} Confirmed",
            message="Your order has been confirmed and is being prepared for shipping.",
            type='order',
            order_id=order.id
        )
        
        flash('Order confirmed successfully!', 'success')
    except Exception as e:
        logger.error(f"Confirm order error: {e}")
        flash('Error confirming order.', 'danger')
    
    return redirect(url_for('order_detail', id=id))

@app.route('/order/<int:id>/cancel', methods=['POST'])
@login_required
def cancel_order(id):
    try:
        order = Order.query.get_or_404(id)
        user = User.query.get(session['user_id'])
        
        if user.role != 'admin' and order.user_id != user.id and order.seller_id != user.id:
            flash('Access denied.', 'danger')
            return redirect(url_for('orders'))
        
        if order.status not in ['pending', 'confirmed']:
            flash('Order cannot be cancelled at this stage.', 'danger')
            return redirect(url_for('order_detail', id=id))
        
        for item in order.items:
            product = item.product
            product.reserved_stock -= item.quantity
            log_inventory_change(
                product.id,
                product.seller_id,
                product.stock - product.reserved_stock + item.quantity,
                product.stock - product.reserved_stock,
                'cancel',
                order.id
            )
        
        order.status = 'cancelled'
        order.cancelled_at = datetime.utcnow()
        db.session.commit()
        
        if user.id == order.user_id:
            if order.seller_id:
                create_notification(
                    user_id=order.seller_id,
                    title=f"Order #{order.order_number} Cancelled",
                    message="The buyer has cancelled this order.",
                    type='order',
                    order_id=order.id
                )
        elif user.id == order.seller_id:
            create_notification(
                user_id=order.user_id,
                title=f"Order #{order.order_number} Cancelled",
                message="The seller has cancelled your order.",
                type='order',
                order_id=order.id
            )
        
        flash('Order cancelled successfully!', 'success')
    except Exception as e:
        logger.error(f"Cancel order error: {e}")
        flash('Error cancelling order.', 'danger')
    
    return redirect(url_for('order_detail', id=id))

# ===== RIDER ROUTES =====

@app.route('/rider/dashboard')
@role_required('rider')
def rider_dashboard():
    try:
        rider = User.query.get(session['user_id'])
        
        deliveries = Order.query.filter_by(rider_id=rider.id).order_by(Order.created_at.desc()).all()
        
        completed = sum(1 for o in deliveries if o.status == 'delivered')
        in_transit = sum(1 for o in deliveries if o.status == 'shipped')
        pending = sum(1 for o in deliveries if o.status == 'confirmed')
        
        available_orders = Order.query.filter(
            Order.rider_id == None,
            Order.status == 'confirmed'
        ).order_by(Order.created_at).all()
        
        stats = {
            'completed': completed,
            'in_transit': in_transit,
            'pending': pending,
            'total_earnings': sum(o.rider_fee for o in deliveries if o.status == 'delivered')
        }
        
        return render_template('rider_dashboard.html', 
                             rider=rider,
                             deliveries=deliveries,
                             available_orders=available_orders,
                             stats=stats)
    except Exception as e:
        logger.error(f"Rider dashboard error: {e}")
        flash('Error loading dashboard.', 'danger')
        return redirect(url_for('index'))

@app.route('/rider/accept_order/<int:id>', methods=['POST'])
@role_required('rider')
def accept_order(id):
    try:
        order = Order.query.get_or_404(id)
        
        if order.rider_id is not None:
            flash('Order already assigned to another rider.', 'danger')
            return redirect(url_for('rider_dashboard'))
        
        if order.status != 'confirmed':
            flash('Order is not ready for delivery.', 'danger')
            return redirect(url_for('rider_dashboard'))
        
        order.rider_id = session['user_id']
        order.status = 'shipped'
        order.shipped_at = datetime.utcnow()
        db.session.commit()
        
        for item in order.items:
            product = item.product
            previous_stock = product.stock
            product.stock -= item.quantity
            product.reserved_stock -= item.quantity
            log_inventory_change(
                product.id,
                product.seller_id,
                previous_stock,
                product.stock,
                'shipped',
                order.id
            )
        
        for item in order.items:
            sale = Sale(
                order_id=order.id,
                seller_id=item.seller_id,
                product_id=item.product_id,
                quantity=item.quantity,
                unit_price=item.price,
                total_price=item.price * item.quantity,
                profit=(item.price - item.seller_price) * item.quantity,
                sale_type='online',
                status='completed',
                payment_method=order.payment_method
            )
            db.session.add(sale)
        
        db.session.commit()
        
        create_notification(
            user_id=order.user_id,
            title=f"Order #{order.order_number} Shipped",
            message="Your order is on its way! Track it in real-time.",
            type='order',
            order_id=order.id
        )
        
        if order.seller_id:
            create_notification(
                user_id=order.seller_id,
                title=f"Order #{order.order_number} Shipped",
                message="Your order has been picked up by a rider.",
                type='order',
                order_id=order.id
            )
        
        flash('Order accepted! You can now deliver it.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Accept order error: {e}")
        flash('Error accepting order.', 'danger')
    
    return redirect(url_for('rider_dashboard'))

@app.route('/rider/update_status/<int:id>', methods=['POST'])
@role_required('rider')
def update_delivery_status(id):
    try:
        order = Order.query.get_or_404(id)
        if order.rider_id != session['user_id']:
            flash('Access denied.', 'danger')
            return redirect(url_for('rider_dashboard'))
        
        new_status = request.form['status']
        
        if new_status == 'delivered' and order.status == 'shipped':
            order.status = 'delivered'
            order.delivered_at = datetime.utcnow()
            
            if order.payment_method == 'cash':
                order.payment_status = 'paid'
            
            create_notification(
                user_id=order.user_id,
                title=f"Order #{order.order_number} Delivered",
                message="Your order has been delivered! Enjoy your products.",
                type='order',
                order_id=order.id
            )
            
            if order.seller_id:
                create_notification(
                    user_id=order.seller_id,
                    title=f"Order #{order.order_number} Delivered",
                    message="Your order has been successfully delivered.",
                    type='order',
                    order_id=order.id
                )
            
            flash('Order marked as delivered!', 'success')
        else:
            flash('Invalid status update.', 'danger')
        
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Update status error: {e}")
        flash('Error updating status.', 'danger')
    
    return redirect(url_for('rider_dashboard'))

@app.route('/rider/update-location', methods=['POST'])
@login_required
@role_required('rider')
def update_rider_location():
    try:
        data = request.get_json()
        rider_id = session['user_id']
        lat = data.get('lat')
        lng = data.get('lng')
        accuracy = data.get('accuracy', 0)
        order_id = data.get('order_id')
        
        rider = User.query.get(rider_id)
        rider.latitude = lat
        rider.longitude = lng
        rider.last_seen = datetime.utcnow()
        
        try:
            location = UserLocation(
                user_id=rider_id,
                latitude=lat,
                longitude=lng,
                accuracy=accuracy
            )
            db.session.add(location)
        except:
            pass
        
        if order_id:
            try:
                tracking = OrderTracking(
                    order_id=order_id,
                    rider_lat=lat,
                    rider_lng=lng,
                    status='in_transit',
                    timestamp=datetime.utcnow()
                )
                db.session.add(tracking)
                
                socketio.emit(f'order_{order_id}_location', {
                    'lat': lat,
                    'lng': lng,
                    'timestamp': datetime.utcnow().isoformat()
                }, room=f'order_{order_id}')
            except:
                pass
        
        db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Update location error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/rider-location/<int:rider_id>')
def get_rider_location(rider_id):
    try:
        rider = User.query.get_or_404(rider_id)
        return jsonify({
            'lat': rider.latitude,
            'lng': rider.longitude,
            'last_seen': rider.last_seen.isoformat() if rider.last_seen else None,
            'is_online': rider.is_online
        })
    except Exception as e:
        logger.error(f"Get rider location error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/order-tracking/<int:order_id>')
@login_required
def get_order_tracking(order_id):
    try:
        tracking = OrderTracking.query.filter_by(order_id=order_id).order_by(OrderTracking.timestamp).all()
        
        return jsonify([{
            'lat': t.rider_lat,
            'lng': t.rider_lng,
            'status': t.status,
            'message': t.message,
            'timestamp': t.timestamp.isoformat()
        } for t in tracking])
    except Exception as e:
        logger.error(f"Order tracking error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/rider/track/<int:order_id>')
@login_required
def track_order(order_id):
    try:
        order = Order.query.get_or_404(order_id)
        user = User.query.get(session['user_id'])
        
        if user.role != 'admin' and order.user_id != user.id and order.rider_id != user.id:
            flash('Access denied.', 'danger')
            return redirect(url_for('index'))
        
        return render_template('track_order.html', order=order)
    except Exception as e:
        logger.error(f"Track order error: {e}")
        flash('Error loading tracking.', 'danger')
        return redirect(url_for('orders'))

# ===== SELLER DASHBOARD =====

@app.route('/seller/dashboard')
@role_required('seller')
def seller_dashboard():
    try:
        seller = User.query.get(session['user_id'])
        
        if not seller:
            flash('Seller not found.', 'danger')
            return redirect(url_for('index'))
        
        products = Product.query.filter_by(seller_id=seller.id, is_active=True).all()
        
        orders = Order.query.filter_by(seller_id=seller.id).order_by(Order.created_at.desc()).all()
        
        pending_orders = [o for o in orders if o.status == 'pending']
        confirmed_orders = [o for o in orders if o.status == 'confirmed']
        shipped_orders = [o for o in orders if o.status == 'shipped']
        delivered_orders = [o for o in orders if o.status == 'delivered']
        
        today = datetime.now().date()
        today_sales = 0
        week_sales = 0
        month_sales = 0
        total_sales = 0
        
        sales = Sale.query.filter_by(seller_id=seller.id, status='completed').all()
        for sale in sales:
            total_sales += sale.total_price
            if sale.sale_date.date() == today:
                today_sales += sale.total_price
            if sale.sale_date >= datetime.now() - timedelta(days=7):
                week_sales += sale.total_price
            if sale.sale_date.month == today.month and sale.sale_date.year == today.year:
                month_sales += sale.total_price
        
        recent_customers = Customer.query.filter_by(seller_id=seller.id).order_by(Customer.last_purchase.desc()).limit(5).all()
        
        low_stock_products = [p for p in products if p.stock <= p.low_stock_threshold]
        
        unread_messages = Message.query.filter_by(receiver_id=seller.id, is_read=False).count()
        
        pending_inquiries = ProductInquiry.query.filter_by(seller_id=seller.id, status='pending').count()
        
        unread_notifications = Notification.query.filter_by(user_id=seller.id, is_read=False).count()
        
        from sqlalchemy import func
        top_products = db.session.query(
            Product, func.sum(OrderItem.quantity).label('total_sold')
        ).join(OrderItem).filter(Product.seller_id == seller.id).group_by(Product).order_by(func.sum(OrderItem.quantity).desc()).limit(5).all()
        
        stats = {
            'total_products': len(products),
            'total_orders': len(orders),
            'pending_orders': len(pending_orders),
            'confirmed_orders': len(confirmed_orders),
            'shipped_orders': len(shipped_orders),
            'delivered_orders': len(delivered_orders),
            'total_sales': total_sales,
            'today_sales': today_sales,
            'week_sales': week_sales,
            'month_sales': month_sales,
            'low_stock_count': len(low_stock_products),
            'unread_messages': unread_messages,
            'pending_inquiries': pending_inquiries,
            'unread_notifications': unread_notifications
        }
        
        recent_orders = orders[:5]
        
        return render_template('seller_dashboard.html', 
                             seller=seller,
                             products=products,
                             stats=stats,
                             recent_orders=recent_orders,
                             low_stock_products=low_stock_products,
                             top_products=top_products,
                             recent_customers=recent_customers)
    except Exception as e:
        logger.error(f"Seller dashboard error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        flash('Error loading dashboard. Please try again.', 'danger')
        return redirect(url_for('index'))

@app.route('/seller/orders')
@role_required('seller')
def seller_orders():
    try:
        seller = User.query.get(session['user_id'])
        orders = Order.query.filter_by(seller_id=seller.id).order_by(Order.created_at.desc()).all()
        return render_template('seller_orders.html', orders=orders)
    except Exception as e:
        logger.error(f"Seller orders error: {e}")
        flash('Error loading orders.', 'danger')
        return redirect(url_for('seller_dashboard'))

@app.route('/seller/products')
@role_required('seller')
def seller_products():
    try:
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
    except Exception as e:
        logger.error(f"Seller products error: {e}")
        flash('Error loading products.', 'danger')
        return redirect(url_for('seller_dashboard'))

@app.route('/seller/add_product', methods=['GET', 'POST'])
@role_required('seller')
def add_product():
    if request.method == 'POST':
        try:
            name = request.form['name']
            description = request.form['description']
            base_price = float(request.form['base_price'])
            category = request.form['category']
            stock = int(request.form['stock'])
            sku = request.form.get('sku', f"SKU-{uuid.uuid4().hex[:8].upper()}")
            barcode = request.form.get('barcode', '')
            low_stock_threshold = int(request.form.get('low_stock_threshold', 5))
            video_call_enabled = request.form.get('video_call_enabled') == 'on'
            
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
                reserved_stock=0,
                sku=sku,
                barcode=barcode,
                seller_id=session['user_id'],
                low_stock_threshold=low_stock_threshold,
                video_call_enabled=video_call_enabled
            )
            
            db.session.add(product)
            db.session.commit()
            
            log_inventory_change(product.id, session['user_id'], 0, stock, 'initial_stock')
            
            flash(f'Product added successfully! Final price: Kes{final_price}', 'success')
            return redirect(url_for('seller_products'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Add product error: {e}")
            flash('Error adding product. Please try again.', 'danger')
            return redirect(url_for('add_product'))
    
    return render_template('add_product.html')

@app.route('/seller/edit_product/<int:id>', methods=['GET', 'POST'])
@role_required('seller')
def edit_product(id):
    product = Product.query.get_or_404(id)
    
    if product.seller_id != session['user_id']:
        flash('You do not have permission to edit this product.', 'danger')
        return redirect(url_for('seller_products'))
    
    if request.method == 'POST':
        try:
            previous_stock = product.stock
            
            product.name = request.form['name']
            product.description = request.form['description']
            product.base_price = float(request.form['base_price'])
            final_price, _, _ = calculate_price_with_fees(product.base_price)
            product.price = final_price
            product.category = request.form['category']
            product.stock = int(request.form['stock'])
            product.sku = request.form.get('sku', product.sku)
            product.barcode = request.form.get('barcode', '')
            product.low_stock_threshold = int(request.form.get('low_stock_threshold', 5))
            product.video_call_enabled = request.form.get('video_call_enabled') == 'on'
            
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
        except Exception as e:
            db.session.rollback()
            logger.error(f"Edit product error: {e}")
            flash('Error updating product.', 'danger')
    
    return render_template('edit_product.html', product=product)

@app.route('/seller/delete_product/<int:id>')
@role_required('seller')
def delete_product(id):
    product = Product.query.get_or_404(id)
    
    if product.seller_id != session['user_id']:
        flash('You do not have permission to delete this product.', 'danger')
        return redirect(url_for('seller_products'))
    
    try:
        product.is_active = False
        db.session.commit()
        flash('Product deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Delete product error: {e}")
        flash('Error deleting product.', 'danger')
    
    return redirect(url_for('seller_products'))

@app.route('/seller/inventory')
@role_required('seller')
def seller_inventory():
    try:
        seller = User.query.get(session['user_id'])
        products = Product.query.filter_by(seller_id=seller.id, is_active=True).all()
        
        logs = InventoryLog.query.filter_by(seller_id=seller.id).order_by(InventoryLog.created_at.desc()).limit(50).all()
        
        total_stock_value = sum(p.price * p.stock for p in products)
        total_stock_cost = sum(p.base_price * p.stock for p in products)
        reserved_value = sum(p.price * p.reserved_stock for p in products)
        available_value = total_stock_value - reserved_value
        
        stats = {
            'total_products': len(products),
            'total_stock_value': total_stock_value,
            'total_stock_cost': total_stock_cost,
            'potential_profit': total_stock_value - total_stock_cost,
            'reserved_value': reserved_value,
            'available_value': available_value,
            'low_stock_count': sum(1 for p in products if p.stock <= p.low_stock_threshold),
            'out_of_stock_count': sum(1 for p in products if p.stock == 0)
        }
        
        return render_template('seller_inventory.html', 
                             products=products, 
                             logs=logs,
                             stats=stats)
    except Exception as e:
        logger.error(f"Inventory error: {e}")
        flash('Error loading inventory.', 'danger')
        return redirect(url_for('seller_dashboard'))

@app.route('/seller/restock/<int:id>', methods=['POST'])
@role_required('seller')
def restock_product(id):
    try:
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
    except Exception as e:
        logger.error(f"Restock error: {e}")
        flash('Error restocking product.', 'danger')
    
    return redirect(url_for('seller_inventory'))

# ===== CUSTOMER MANAGEMENT =====

@app.route('/seller/customers')
@role_required('seller')
def customer_list():
    try:
        seller = User.query.get(session['user_id'])
        customers = Customer.query.filter_by(seller_id=seller.id).order_by(Customer.total_purchases.desc()).all()
        
        total_customers = len(customers)
        total_revenue = sum(c.total_purchases for c in customers)
        avg_purchase = total_revenue / total_customers if total_customers > 0 else 0
        
        stats = {
            'total_customers': total_customers,
            'total_revenue': total_revenue,
            'avg_purchase': avg_purchase,
            'repeat_customers': sum(1 for c in customers if c.purchase_count > 1)
        }
        
        return render_template('customer_list.html', customers=customers, stats=stats)
    except Exception as e:
        logger.error(f"Customer list error: {e}")
        flash('Error loading customers.', 'danger')
        return redirect(url_for('seller_dashboard'))

@app.route('/seller/customer/<int:id>')
@role_required('seller')
def customer_detail(id):
    try:
        customer = Customer.query.get_or_404(id)
        
        if customer.seller_id != session['user_id']:
            flash('Access denied.', 'danger')
            return redirect(url_for('customer_list'))
        
        purchases = Sale.query.filter_by(seller_id=session['user_id'], customer_id=customer.id).order_by(Sale.sale_date.desc()).all()
        
        return render_template('customer_detail.html', customer=customer, purchases=purchases)
    except Exception as e:
        logger.error(f"Customer detail error: {e}")
        flash('Error loading customer details.', 'danger')
        return redirect(url_for('customer_list'))

@app.route('/seller/customer/add', methods=['POST'])
@role_required('seller')
def add_customer():
    try:
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
    except Exception as e:
        db.session.rollback()
        logger.error(f"Add customer error: {e}")
        flash('Error adding customer.', 'danger')
    
    return redirect(url_for('customer_list'))

# ===== SALES REPORTS =====

@app.route('/seller/sales')
@role_required('seller')
def sales_report():
    try:
        seller = User.query.get(session['user_id'])
        
        period = request.args.get('period', 'month')
        today = datetime.now().date()
        
        if period == 'day':
            start_date = today
            group_by = 'hour'
        elif period == 'week':
            start_date = today - timedelta(days=today.weekday())
            group_by = 'day'
        elif period == 'month':
            start_date = today.replace(day=1)
            group_by = 'day'
        else:
            start_date = today.replace(month=1, day=1)
            group_by = 'month'
        
        from sqlalchemy import func
        
        sales = Sale.query.filter(
            Sale.seller_id == seller.id,
            Sale.status == 'completed',
            Sale.sale_date >= start_date
        ).order_by(Sale.sale_date).all()
        
        total_sales = sum(s.total_price for s in sales)
        total_orders = len(sales)
        avg_order_value = total_sales / total_orders if total_orders > 0 else 0
        total_profit = sum(s.profit for s in sales if s.profit) if sales else 0
        
        sales_by_product = db.session.query(
            Product.name, 
            func.sum(Sale.quantity).label('quantity'),
            func.sum(Sale.total_price).label('total'),
            func.count(Sale.id).label('count')
        ).join(Sale, Sale.product_id == Product.id).filter(
            Sale.seller_id == seller.id,
            Sale.status == 'completed',
            Sale.sale_date >= start_date
        ).group_by(Product.id).order_by(func.sum(Sale.total_price).desc()).all()
        
        if group_by == 'hour':
            sales_by_day = db.session.query(
                func.date_trunc('hour', Sale.sale_date).label('date'),
                func.sum(Sale.total_price).label('total'),
                func.count(Sale.id).label('count')
            ).filter(
                Sale.seller_id == seller.id,
                Sale.status == 'completed',
                Sale.sale_date >= start_date
            ).group_by(func.date_trunc('hour', Sale.sale_date)).order_by('date').all()
        else:
            sales_by_day = db.session.query(
                func.date(Sale.sale_date).label('date'),
                func.sum(Sale.total_price).label('total'),
                func.count(Sale.id).label('count')
            ).filter(
                Sale.seller_id == seller.id,
                Sale.status == 'completed',
                Sale.sale_date >= start_date
            ).group_by(func.date(Sale.sale_date)).order_by('date').all()
        
        top_customers = db.session.query(
            Customer.name,
            Customer.phone,
            func.sum(Sale.total_price).label('total'),
            func.count(Sale.id).label('orders')
        ).join(Sale, Sale.customer_id == Customer.id).filter(
            Sale.seller_id == seller.id,
            Sale.status == 'completed',
            Sale.sale_date >= start_date
        ).group_by(Customer.id).order_by(func.sum(Sale.total_price).desc()).limit(5).all()
        
        stats = {
            'total_sales': total_sales,
            'total_orders': total_orders,
            'avg_order_value': avg_order_value,
            'total_profit': total_profit,
            'profit_margin': (total_profit / total_sales * 100) if total_sales > 0 else 0
        }
        
        return render_template('sales_report.html',
                             period=period,
                             stats=stats,
                             sales_by_product=sales_by_product,
                             sales_by_day=sales_by_day,
                             top_customers=top_customers)
    except Exception as e:
        logger.error(f"Sales report error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        flash('Error loading sales report.', 'danger')
        return redirect(url_for('seller_dashboard'))

# ===== NOTIFICATION ROUTES =====

@app.route('/notifications')
@login_required
def notifications():
    try:
        user_id = session['user_id']
        notifications = Notification.query.filter_by(user_id=user_id).order_by(Notification.created_at.desc()).all()
        
        for n in notifications:
            n.is_read = True
        db.session.commit()
        
        return render_template('notifications.html', notifications=notifications)
    except Exception as e:
        logger.error(f"Notifications error: {e}")
        flash('Error loading notifications.', 'danger')
        return redirect(url_for('index'))

@app.route('/api/notifications')
@login_required
def get_notifications():
    try:
        user_id = session['user_id']
        unread_count = Notification.query.filter_by(user_id=user_id, is_read=False).count()
        latest = Notification.query.filter_by(user_id=user_id, is_read=False).order_by(Notification.created_at.desc()).first()
        
        return jsonify({
            'unread_count': unread_count,
            'latest': {
                'id': latest.id,
                'title': latest.title,
                'message': latest.message,
                'type': latest.type,
                'created_at': latest.created_at.isoformat()
            } if latest else None
        })
    except Exception as e:
        logger.error(f"Notifications API error: {e}")
        return jsonify({'unread_count': 0, 'latest': None})

@app.route('/api/notifications/<int:id>/read', methods=['POST'])
@login_required
def mark_notification_read(id):
    try:
        notification = Notification.query.get_or_404(id)
        if notification.user_id != session['user_id']:
            return jsonify({'error': 'Unauthorized'}), 403
        
        notification.is_read = True
        db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Mark notification read error: {e}")
        return jsonify({'error': str(e)}), 500

# ===== POS SYSTEM =====

@app.route('/seller/pos')
@role_required('seller')
def pos_dashboard():
    try:
        seller = User.query.get(session['user_id'])
        products = Product.query.filter_by(seller_id=seller.id, is_active=True).filter(Product.stock > 0).all()
        
        from sqlalchemy import func
        
        today = datetime.now().date()
        today_sales = OfflineSale.query.filter(
            OfflineSale.seller_id == seller.id,
            func.date(OfflineSale.created_at) == today
        ).order_by(OfflineSale.created_at.desc()).all()
        
        today_total = sum(sale.total for sale in today_sales)
        
        return render_template('pos_dashboard.html', 
                             products=products,
                             today_sales=today_sales,
                             today_total=today_total)
    except Exception as e:
        logger.error(f"POS dashboard error: {e}")
        flash('Error loading POS.', 'danger')
        return redirect(url_for('seller_dashboard'))

@app.route('/seller/pos/checkout', methods=['POST'])
@role_required('seller')
def pos_checkout():
    try:
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
                profit=(product.price - product.base_price) * item['quantity'],
                sale_type='pos',
                status='completed',
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
                customer.purchase_count += 1
                customer.last_purchase = datetime.utcnow()
                if customer_whatsapp:
                    customer.whatsapp = customer_whatsapp
            else:
                customer = Customer(
                    seller_id=seller.id,
                    name=customer_name,
                    phone=customer_phone,
                    whatsapp=customer_whatsapp,
                    total_purchases=total,
                    purchase_count=1,
                    last_purchase=datetime.utcnow()
                )
                db.session.add(customer)
            
            for sale in Sale.query.filter_by(seller_id=seller.id).order_by(Sale.sale_date.desc()).limit(len(items)).all():
                if not sale.customer_id:
                    sale.customer_id = customer.id
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'receipt_number': receipt_number,
            'total': total
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"POS checkout error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/seller/pos/receipt/<receipt_number>')
@role_required('seller')
def pos_receipt(receipt_number):
    try:
        sale = OfflineSale.query.filter_by(receipt_number=receipt_number).first_or_404()
        
        if sale.seller_id != session['user_id']:
            flash('Access denied.', 'danger')
            return redirect(url_for('pos_dashboard'))
        
        return render_template('pos_receipt.html', sale=sale)
    except Exception as e:
        logger.error(f"Receipt error: {e}")
        flash('Receipt not found.', 'danger')
        return redirect(url_for('pos_dashboard'))

# ===== CHAT ROUTES =====

@app.route('/chat/<int:receiver_id>')
@login_required
def chat_with_user(receiver_id):
    try:
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
    except Exception as e:
        logger.error(f"Chat error: {e}")
        flash('Error loading chat.', 'danger')
        return redirect(url_for('inbox'))

@app.route('/send_message', methods=['POST'])
@login_required
def send_message():
    try:
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
        
        sender = User.query.get(session['user_id'])
        create_notification(
            user_id=receiver_id,
            title=f"New Message from {sender.username}",
            message=message[:100] + ('...' if len(message) > 100 else ''),
            type='message'
        )
        
        return redirect(url_for('chat_with_user', receiver_id=receiver_id, product_id=product_id))
    except Exception as e:
        db.session.rollback()
        logger.error(f"Send message error: {e}")
        flash('Error sending message.', 'danger')
        return redirect(request.referrer or url_for('index'))

@app.route('/inbox')
@login_required
def inbox():
    try:
        user_id = session['user_id']
        
        sent = db.session.query(Message.receiver_id).filter(Message.sender_id == user_id).distinct().subquery()
        received = db.session.query(Message.sender_id).filter(Message.receiver_id == user_id).distinct().subquery()
        
        conversation_partner_ids = db.session.query(sent.c.receiver_id).union(db.session.query(received.c.sender_id)).distinct().all()
        conversation_partner_ids = [id[0] for id in conversation_partner_ids]
        
        conversations = []
        for partner_id in conversation_partner_ids:
            partner = User.query.get(partner_id)
            if not partner:
                continue
                
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
    except Exception as e:
        logger.error(f"Inbox error: {e}")
        return render_template('inbox.html', conversations=[])

# ===== COMMUNITY ROUTES =====

@app.route('/community', methods=['GET', 'POST'])
@login_required
def community():
    if request.method == 'POST':
        try:
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
            
            db.session.add(post)
            db.session.commit()
            flash('Post added to community!', 'success')
        except Exception as e:
            db.session.rollback()
            logger.error(f"Create post error: {e}")
            flash('Error creating post. Please try again.', 'danger')
        
        return redirect(url_for('community'))
    
    try:
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
    except Exception as e:
        logger.error(f"Community error: {e}")
        return render_template('community.html', posts=[], pagination=None, users=[], post_types=[], current_filter='all')

@app.route('/post/<int:id>', methods=['GET', 'POST'])
@login_required
def view_post(id):
    try:
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
            
            db.session.add(comment)
            db.session.commit()
            flash('Comment added!', 'success')
            return redirect(url_for('view_post', id=post.id))
        
        comments = Comment.query.filter_by(post_id=post.id).order_by(Comment.created_at).all()
        
        return render_template('view_post.html', post=post, comments=comments)
    except Exception as e:
        logger.error(f"View post error: {e}")
        flash('Error loading post.', 'danger')
        return redirect(url_for('community'))

@app.route('/post/<int:id>/like')
@login_required
def like_post(id):
    try:
        post = Post.query.get_or_404(id)
        post.likes += 1
        db.session.commit()
        flash('Post liked!', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Like post error: {e}")
        flash('Error liking post.', 'danger')
    
    return redirect(request.referrer or url_for('community'))

@app.route('/post/<int:id>/delete', methods=['POST'])
@login_required
def delete_post(id):
    try:
        post = Post.query.get_or_404(id)
        
        if post.user_id != session['user_id'] and session['role'] != 'admin':
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        
        Comment.query.filter_by(post_id=id).delete()
        db.session.delete(post)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Delete post error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ===== REVIEW ROUTES =====

@app.route('/review/<int:user_id>', methods=['GET', 'POST'])
@login_required
def review_user(user_id):
    try:
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
            
            create_notification(
                user_id=user_id,
                title=f"New Review from {session['username']}",
                message=f"Rating: {request.form['rating']} stars - {request.form['comment'][:100]}...",
                type='review'
            )
            
            flash('Review submitted successfully!', 'success')
            return redirect(url_for('community'))
        
        return render_template('review.html', user=reviewed_user, order_id=order_id)
    except Exception as e:
        logger.error(f"Review error: {e}")
        flash('Error processing review.', 'danger')
        return redirect(url_for('community'))

# ===== AI ASSISTANT =====

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
                        order_list.append(f"Order #{order.order_number}: {', '.join(items)}")
                    context += f"They recently ordered: {'; '.join(order_list)}. "
                
                product_count = Product.query.filter_by(is_active=True).count()
                seller_count = User.query.filter_by(role='seller').count()
                context += f"The marketplace has {product_count} products from {seller_count} sellers. "
                
                response = co.chat(
                    message=user_message,
                    model="command",
                    preamble=f"You are a helpful assistant for Fresh Marketplace. {context}",
                    temperature=0.7
                )
                
                if hasattr(response, 'text'):
                    response_text = response.text
                else:
                    response_text = str(response)
                    
            except Exception as e:
                response_text = f"AI service temporarily unavailable."
                logger.error(f"AI Error: {str(e)}")
        else:
            response_text = "AI assistant is currently unavailable."
    
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

@app.route('/api/ai/quick', methods=['POST'])
def ai_quick_question():
    if not co:
        return jsonify({'response': 'AI assistant is currently unavailable.'})
    
    data = request.get_json()
    question = data.get('message', '')
    
    if not question:
        return jsonify({'response': 'Please ask a question.'})
    
    try:
        response = co.chat(
            message=question,
            model="command",
            preamble="You are a helpful assistant for Fresh Marketplace.",
            temperature=0.7
        )
        
        if hasattr(response, 'text'):
            answer = response.text
        else:
            answer = str(response)
        
        return jsonify({'response': answer})
    except Exception as e:
        logger.error(f"AI Quick Error: {e}")
        return jsonify({'response': 'AI service temporarily unavailable.'})

# ===== ADMIN DASHBOARD =====

@app.route('/admin/dashboard')
@role_required('admin')
def admin_dashboard():
    try:
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
        completed_orders = Order.query.filter_by(status='delivered').count()
        
        from sqlalchemy import func
        total_revenue = db.session.query(func.coalesce(func.sum(Order.total), 0)).filter(Order.status == 'delivered').scalar() or 0
        total_platform_fees = db.session.query(func.coalesce(func.sum(Order.platform_fee), 0)).filter(Order.status == 'delivered').scalar() or 0
        total_rider_fees = db.session.query(func.coalesce(func.sum(Order.rider_fee), 0)).filter(Order.status == 'delivered').scalar() or 0
        
        stats = {
            'total_users': total_users,
            'total_sellers': total_sellers,
            'total_riders': total_riders,
            'total_buyers': total_buyers,
            'total_products': total_products,
            'total_orders': total_orders,
            'pending_orders': pending_orders,
            'completed_orders': completed_orders,
            'total_revenue': float(total_revenue),
            'total_platform_fees': float(total_platform_fees),
            'total_rider_fees': float(total_rider_fees)
        }
        
        return render_template('admin_dashboard.html', 
                             stats=stats, 
                             users=users[:10], 
                             products=products[:10], 
                             orders=orders)
    except Exception as e:
        logger.error(f"Admin dashboard error: {e}")
        flash('Error loading dashboard.', 'danger')
        return redirect(url_for('index'))

# ===== ADMIN USER MANAGEMENT =====

@app.route('/admin/users')
@role_required('admin')
def admin_users():
    try:
        users = User.query.order_by(User.created_at.desc()).all()
        return render_template('admin_users.html', users=users)
    except Exception as e:
        logger.error(f"Admin users error: {e}")
        flash('Error loading users.', 'danger')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/user/<int:user_id>')
@role_required('admin')
def admin_user_detail(user_id):
    try:
        user = User.query.get_or_404(user_id)
        return render_template('admin_user_detail.html', user=user)
    except Exception as e:
        logger.error(f"Admin user detail error: {e}")
        flash('Error loading user details.', 'danger')
        return redirect(url_for('admin_users'))

@app.route('/admin/user/<int:user_id>/edit', methods=['GET', 'POST'])
@role_required('admin')
def admin_edit_user(user_id):
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        try:
            user.username = request.form['username']
            user.email = request.form['email']
            user.role = request.form['role']
            user.phone_number = request.form['phone_number']
            user.whatsapp_number = request.form.get('whatsapp_number', '')
            user.location = request.form.get('location', '')
            user.business_name = request.form.get('business_name', '')
            user.business_address = request.form.get('business_address', '')
            
            if request.form.get('new_password'):
                user.password = generate_password_hash(request.form['new_password'])
            
            if 'profile_image' in request.files:
                file = request.files['profile_image']
                if file and file.filename and allowed_file(file.filename):
                    filename = secure_filename(f"{user.username}_{uuid.uuid4().hex[:8]}_{file.filename}")
                    file.save(os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], filename))
                    user.profile_image = f'/static/uploads/{filename}'
            
            db.session.commit()
            flash('User updated successfully!', 'success')
            return redirect(url_for('admin_user_detail', user_id=user.id))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Admin edit user error: {e}")
            flash('Error updating user.', 'danger')
    
    return render_template('admin_edit_user.html', user=user)

@app.route('/admin/user/<int:user_id>/delete', methods=['POST'])
@role_required('admin')
def admin_delete_user(user_id):
    user = User.query.get_or_404(user_id)
    
    if user.id == session['user_id']:
        flash('You cannot delete your own account.', 'danger')
        return redirect(url_for('admin_users'))
    
    try:
        db.session.delete(user)
        db.session.commit()
        flash('User deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Admin delete user error: {e}")
        flash('Error deleting user.', 'danger')
    
    return redirect(url_for('admin_users'))

@app.route('/admin/impersonate/<int:user_id>')
@role_required('admin')
def impersonate_user(user_id):
    user = User.query.get_or_404(user_id)
    
    session['admin_original_id'] = session['user_id']
    session['admin_original_name'] = session['username']
    
    session['user_id'] = user.id
    session['username'] = user.username
    session['role'] = user.role
    session['profile_image'] = user.profile_image
    session['impersonating'] = True
    
    flash(f'Now impersonating {user.username}.', 'success')
    
    if user.role == 'seller':
        return redirect(url_for('seller_dashboard'))
    elif user.role == 'rider':
        return redirect(url_for('rider_dashboard'))
    else:
        return redirect(url_for('index'))

@app.route('/admin/stop-impersonating')
@login_required
def stop_impersonating():
    if 'admin_original_id' in session:
        session['user_id'] = session['admin_original_id']
        session['username'] = session['admin_original_name']
        session['role'] = 'admin'
        session['profile_image'] = User.query.get(session['user_id']).profile_image
        
        session.pop('admin_original_id', None)
        session.pop('admin_original_name', None)
        session.pop('impersonating', None)
        
        flash('Stopped impersonating.', 'success')
        return redirect(url_for('admin_users'))
    
    return redirect(url_for('index'))

@app.route('/admin/products')
@role_required('admin')
def admin_products():
    try:
        products = Product.query.order_by(Product.created_at.desc()).all()
        return render_template('admin_products.html', products=products)
    except Exception as e:
        logger.error(f"Admin products error: {e}")
        flash('Error loading products.', 'danger')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/product/<int:id>/toggle', methods=['POST'])
@role_required('admin')
def admin_toggle_product(id):
    product = Product.query.get_or_404(id)
    product.is_active = not product.is_active
    db.session.commit()
    flash(f'Product {"activated" if product.is_active else "deactivated"} successfully!', 'success')
    return redirect(url_for('admin_products'))

@app.route('/admin/product/<int:id>/delete', methods=['POST'])
@role_required('admin')
def admin_delete_product(id):
    product = Product.query.get_or_404(id)
    
    try:
        db.session.delete(product)
        db.session.commit()
        flash('Product deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Admin delete product error: {e}")
        flash('Error deleting product.', 'danger')
    
    return redirect(url_for('admin_products'))

# ===== VIDEO CALL FEATURES =====

@app.route('/video-call/initiate', methods=['POST'])
@login_required
def initiate_video_call():
    try:
        data = request.get_json()
        receiver_id = data.get('receiver_id')
        product_id = data.get('product_id')
        
        receiver = User.query.get_or_404(receiver_id)
        product = Product.query.get(product_id) if product_id else None
        
        if not receiver.is_online:
            return jsonify({'error': 'User is offline'}), 400
        
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
    except Exception as e:
        logger.error(f"Initiate video call error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/video-call/<int:call_id>')
@login_required
def video_call_room(call_id):
    try:
        call = VideoCall.query.get_or_404(call_id)
        
        if call.initiator_id != session['user_id'] and call.receiver_id != session['user_id']:
            flash('Access denied.', 'danger')
            return redirect(url_for('index'))
        
        if call.status == 'pending':
            call.status = 'active'
            call.started_at = datetime.utcnow()
            db.session.commit()
        
        product = Product.query.get(call.product_id) if call.product_id else None
        other_user = User.query.get(call.initiator_id if call.receiver_id == session['user_id'] else call.receiver_id)
        
        return render_template('video_call.html', 
                             call=call, 
                             product=product,
                             other_user=other_user)
    except Exception as e:
        logger.error(f"Video call room error: {e}")
        flash('Error loading video call.', 'danger')
        return redirect(url_for('index'))

@app.route('/video-call/<int:call_id>/end', methods=['POST'])
@login_required
def end_video_call(call_id):
    try:
        call = VideoCall.query.get_or_404(call_id)
        
        if call.initiator_id != session['user_id'] and call.receiver_id != session['user_id']:
            return jsonify({'error': 'Unauthorized'}), 403
        
        call.status = 'ended'
        call.ended_at = datetime.utcnow()
        db.session.commit()
        
        other_id = call.receiver_id if call.initiator_id == session['user_id'] else call.initiator_id
        socketio.emit('call_ended', {
            'call_id': call_id
        }, room=f'user_{other_id}')
        
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"End video call error: {e}")
        return jsonify({'error': str(e)}), 500

# ===== SOCKETIO EVENTS =====

@socketio.on('connect')
def handle_connect():
    if 'user_id' in session:
        user_id = session['user_id']
        join_room(f'user_{user_id}')
        
        try:
            user = User.query.get(user_id)
            if user:
                user.is_online = True
                user.last_seen = datetime.utcnow()
                db.session.commit()
        except:
            pass

@socketio.on('disconnect')
def handle_disconnect():
    if 'user_id' in session:
        user_id = session['user_id']
        leave_room(f'user_{user_id}')
        
        try:
            user = User.query.get(user_id)
            if user:
                user.is_online = False
                user.last_seen = datetime.utcnow()
                db.session.commit()
        except:
            pass

@socketio.on('join_call')
def handle_join_call(data):
    call_id = data.get('call_id')
    if call_id and 'user_id' in session:
        room = f'call_{call_id}'
        join_room(room)
        emit('user_joined', {
            'user_id': session['user_id'],
            'username': session['username']
        }, room=room)

@socketio.on('leave_call')
def handle_leave_call(data):
    call_id = data.get('call_id')
    if call_id and 'user_id' in session:
        room = f'call_{call_id}'
        leave_room(room)
        emit('user_left', {
            'user_id': session['user_id'],
            'username': session['username']
        }, room=room)

@socketio.on('track_order')
def handle_track_order(data):
    order_id = data.get('order_id')
    if order_id and 'user_id' in session:
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
                location='Main Office'
            )
            db.session.add(admin)
        
        if not User.query.filter_by(username='seller1').first():
            seller = User(
                username='seller1',
                email='seller@marketplace.com',
                password=generate_password_hash('seller123'),
                role='seller',
                phone_number='5551234567',
                location='Market Street',
                business_name='Fresh Farm Produce'
            )
            db.session.add(seller)
        
        if not User.query.filter_by(username='rider1').first():
            rider = User(
                username='rider1',
                email='rider@marketplace.com',
                password=generate_password_hash('rider123'),
                role='rider',
                phone_number='0987654321',
                location='Downtown'
            )
            db.session.add(rider)
        
        if not User.query.filter_by(username='buyer1').first():
            buyer = User(
                username='buyer1',
                email='buyer@marketplace.com',
                password=generate_password_hash('buyer123'),
                role='buyer',
                phone_number='5559876543',
                location='Nairobi'
            )
            db.session.add(buyer)
        
        if not User.query.filter_by(username='benedict431').first():
            superuser = User(
                username='benedict431',
                email='benedict431@admin.com',
                password=generate_password_hash('28734495'),
                role='admin',
                phone_number='+254700000000',
                whatsapp_number='+254700000000',
                location='Nairobi, Kenya',
                business_name='Super Admin'
            )
            db.session.add(superuser)
        
        db.session.commit()
        
        if Product.query.count() == 0:
            seller = User.query.filter_by(role='seller').first() or User.query.filter_by(role='admin').first()
            
            if seller:
                products = [
                    Product(
                        name='Fresh Apples',
                        description='Crisp and sweet red apples from local farms',
                        base_price=3.99,
                        price=4.79,
                        category='Fruits',
                        image_url='https://images.unsplash.com/photo-1560806887-1e4cd0b6cbd6?w=400',
                        stock=50,
                        reserved_stock=0,
                        sku='FRUIT-001',
                        seller_id=seller.id,
                        video_call_enabled=True,
                        is_active=True
                    ),
                    Product(
                        name='Organic Bananas',
                        description='Yellow ripe bananas, rich in potassium',
                        base_price=2.49,
                        price=2.99,
                        category='Fruits',
                        image_url='https://images.unsplash.com/photo-1603833665858-e61d17a86224?w=400',
                        stock=100,
                        reserved_stock=0,
                        sku='FRUIT-002',
                        seller_id=seller.id,
                        video_call_enabled=True,
                        is_active=True
                    ),
                    Product(
                        name='Fresh Tomatoes',
                        description='Ripe red tomatoes perfect for salads',
                        base_price=4.99,
                        price=5.99,
                        category='Vegetables',
                        image_url='https://images.unsplash.com/photo-1546094096-0df4bcaaa337?w=400',
                        stock=75,
                        reserved_stock=0,
                        sku='VEG-001',
                        seller_id=seller.id,
                        video_call_enabled=True,
                        is_active=True
                    )
                ]
                
                for product in products:
                    db.session.add(product)
                
                db.session.commit()
                logger.info("Sample products created")

with app.app_context():
    try:
        db.create_all()
        init_db()
        logger.info("Database initialized successfully")
        logger.info("Demo users: admin/admin123, seller1/seller123, rider1/rider123, buyer1/buyer123, benedict431/28734495")
    except Exception as e:
        logger.error(f"Database initialization error: {e}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
