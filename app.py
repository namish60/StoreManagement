from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import timedelta
from decimal import Decimal

import logging
import boto3
import time
from botocore.exceptions import NoCredentialsError, PartialCredentialsError

app = Flask(__name__)

# ---------------------------------------
# AWS CONFIGURATION
# ---------------------------------------
REGION = 'us-east-2'

# 1. DynamoDB
dynamodb = boto3.resource('dynamodb', region_name=REGION)
products_table = dynamodb.Table('products')
cart_table = dynamodb.Table('user_cart')

# 2. SNS (Admin Alerts)
sns_client = boto3.client('sns', region_name=REGION)

# --- FIX: REMOVED THE SUBSCRIPTION ID FROM THE END ---
# It should look like: arn:aws:sns:region:account-id:topic-name
SNS_TOPIC_ARN = "arn:aws:sns:us-east-2:162646919277:low_stock_alerts" 

# 3. SES (Customer Emails)
ses_client = boto3.client('ses', region_name=REGION)
# ENTER THE VERIFIED SENDER EMAIL HERE
SENDER_EMAIL = "arora.nakul2004@gmail.com" 

# ---------------------------------------
# LOGGING & DB SETUP
# ---------------------------------------
try:
    logging.basicConfig(level=logging.INFO)
except:
    pass

RDS_USERNAME = "admin"
RDS_PASSWORD = "Shivam75#"
RDS_HOSTNAME = "storesphere-rds.cxeayms0yfso.us-east-2.rds.amazonaws.com"
RDS_DB_NAME = "my_db"
RDS_PORT = "3306"

app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+pymysql://{RDS_USERNAME}:{RDS_PASSWORD}@{RDS_HOSTNAME}:{RDS_PORT}/{RDS_DB_NAME}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = "super_secret_key_for_session_management"
app.permanent_session_lifetime = timedelta(minutes=30)

db = SQLAlchemy(app)

class User(db.Model):
    __tablename__ = 'users'
    user_id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

class Payment(db.Model):
    __tablename__ = 'payments'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'), unique=True, nullable=False)
    user_name = db.Column(db.String(100), nullable=False)
    total_quantity = db.Column(db.Integer, default=0)
    total_amount = db.Column(db.Float, default=0.0)

with app.app_context():
    db.create_all()

# --- HELPERS ---
def calculate_threshold(price):
    p = float(price)
    if 100 <= p < 499: return 50
    elif 500 <= p < 1000: return 25
    else: return 15

def decimal_to_native(obj):
    if isinstance(obj, list): return [decimal_to_native(i) for i in obj]
    if isinstance(obj, dict): return {k: decimal_to_native(v) for k, v in obj.items()}
    if isinstance(obj, Decimal): return int(obj) if obj % 1 == 0 else float(obj)
    return obj

# --- ROUTES ---
@app.route('/')
def home(): return render_template('index.html')
@app.route('/register.html')
def register_page(): return render_template('register.html')
@app.route('/admin_dashboard.html')
def admin_dashboard_page(): return render_template('admin_dashboard.html')
@app.route('/view_users.html')
def view_users_page(): return render_template('view_users.html')
@app.route('/user_dashboard.html')
def user_dashboard_page(): return render_template('user_dashboard.html')
@app.route('/payment.html')
def payment_page(): return render_template('payment.html')
@app.route('/bill.html')
def bill_page(): return render_template('bill.html')

# -------------------------------------------------------
# LOGIC: RECORD PAYMENT + SNS + SES
# -------------------------------------------------------
@app.route('/api/record_payment', methods=['POST'])
def record_payment():
    if 'user_id' not in session: return jsonify({'success': False, 'message': 'Login required'}), 401
    
    user_id = session['user_id']
    user_name = session['user_name']
    
    # --- FIX: Updated legacy query to new syntax ---
    current_user = db.session.get(User, user_id)
    user_email = current_user.email

    try:
        # 1. Fetch Cart
        response = cart_table.get_item(Key={'user_id': str(user_id)})
        cart_items = response.get('Item', {}).get('items', [])
        
        if not cart_items:
            return jsonify({'success': False, 'message': 'Cart is empty'})

        total_qty_trans = 0
        total_amt_trans = 0.0
        email_body_items = ""

        # 2. Process Stock & Calculate Totals
        for item in cart_items:
            prod_id = item['id']
            qty_bought = int(item['qty'])
            price = float(item['price'])
            item_total = price * qty_bought
            
            # Aggregate for Payment Table
            total_qty_trans += qty_bought
            total_amt_trans += item_total
            
            # Email String builder
            email_body_items += f"<li>{item['name']} (x{qty_bought}): Rs. {item_total:.2f}</li>"

            # --- A. UPDATE DYNAMODB STOCK ---
            try:
                update_resp = products_table.update_item(
                    Key={'product_id': prod_id},
                    UpdateExpression="set stock = stock - :q",
                    ExpressionAttributeValues={':q': qty_bought},
                    ReturnValues="ALL_NEW" # Get the updated item to check threshold
                )
                
                updated_product = update_resp.get('Attributes', {})
                current_stock = int(updated_product.get('stock', 0))
                threshold = int(updated_product.get('threshold', 10))
                prod_name = updated_product.get('name', 'Unknown Product')

                # --- B. SNS TRIGGER (ADMIN ALERT) ---
                if current_stock < threshold:
                    sns_message = (
                        f"ALERT: Low Stock for {prod_name}!\n"
                        f"Current Stock: {current_stock}\n"
                        f"Threshold: {threshold}\n"
                        f"Please add at least {threshold - current_stock + 10} items."
                    )
                    # --- NOTE: Sending to Topic ARN, NOT Subscription ARN ---
                    sns_client.publish(
                        TopicArn=SNS_TOPIC_ARN,
                        Message=sns_message,
                        Subject=f"Low Stock Alert: {prod_name}"
                    )
                    logging.info(f"SNS Alert sent for {prod_name}")

            except Exception as e:
                logging.error(f"Stock update failed for {prod_id}: {e}")

        # 3. Update RDS Payments (Aggregation)
        payment_record = Payment.query.filter_by(user_id=user_id).first()
        if payment_record:
            payment_record.total_quantity += total_qty_trans
            payment_record.total_amount += total_amt_trans
        else:
            new_payment = Payment(
                user_id=user_id,
                user_name=user_name,
                total_quantity=total_qty_trans,
                total_amount=total_amt_trans
            )
            db.session.add(new_payment)
        db.session.commit()

        # --- C. SES TRIGGER (CUSTOMER RECEIPT) ---
        try:
            email_subject = "StoreSphere - Order Confirmation & Receipt"
            email_html = f"""
            <html>
            <body>
                <h2>Thank you for your purchase, {user_name}!</h2>
                <p>We have received your payment of <b>Rs. {total_amt_trans:.2f}</b>.</p>
                <h3>Order Summary:</h3>
                <ul>
                    {email_body_items}
                </ul>
                <hr>
                <p><strong>Grand Total: Rs. {total_amt_trans:.2f}</strong></p>
                <p>Your order is being processed.</p>
                <br>
                <p>Regards,<br>StoreSphere Team</p>
            </body>
            </html>
            """
            
            ses_client.send_email(
                Source=SENDER_EMAIL,
                Destination={'ToAddresses': [user_email]},
                Message={
                    'Subject': {'Data': email_subject},
                    'Body': {'Html': {'Data': email_html}}
                }
            )
            logging.info(f"Receipt sent to {user_email}")
            
        except Exception as e:
            logging.error(f"SES Error: {e}")
            # Don't fail the transaction just because email failed
            pass

        return jsonify({'success': True})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})

# -------------------------------------------------------
# STANDARD APIs (Existing)
# -------------------------------------------------------
@app.route('/api/cart', methods=['GET'])
def get_cart():
    if 'user_id' not in session: return jsonify([])
    try:
        response = cart_table.get_item(Key={'user_id': str(session['user_id'])})
        return jsonify(decimal_to_native(response.get('Item', {}).get('items', [])))
    except: return jsonify([])

@app.route('/api/cart', methods=['POST'])
def add_to_cart():
    if 'user_id' not in session: return jsonify({'success': False}), 401
    data = request.json
    user_id = str(session['user_id'])
    response = cart_table.get_item(Key={'user_id': user_id})
    current_items = response.get('Item', {}).get('items', [])
    found = False
    for item in current_items:
        if item['id'] == data['id']:
            item['qty'] += 1
            found = True
            break
    if not found:
        current_items.append({'id': data['id'], 'name': data['name'], 'price': Decimal(str(data['price'])), 'qty': 1})
    cart_table.put_item(Item={'user_id': user_id, 'items': current_items})
    return jsonify({'success': True})

@app.route('/api/cart/update_qty', methods=['POST'])
def update_cart_qty():
    if 'user_id' not in session: return jsonify({'success': False}), 401
    data = request.json
    user_id = str(session['user_id'])
    prod_id = data.get('id')
    action = data.get('action')
    response = cart_table.get_item(Key={'user_id': user_id})
    current_items = response.get('Item', {}).get('items', [])
    try:
        prod_resp = products_table.get_item(Key={'product_id': prod_id})
        stock = int(prod_resp['Item']['stock']) if 'Item' in prod_resp else 999
    except: stock = 999
    updated_items = []
    for item in current_items:
        if item['id'] == prod_id:
            if action == 'inc':
                if item['qty'] < stock: item['qty'] += 1
            elif action == 'dec': item['qty'] -= 1
            if item['qty'] > 0: updated_items.append(item)
        else:
            updated_items.append(item)
    cart_table.put_item(Item={'user_id': user_id, 'items': updated_items})
    return jsonify({'success': True})

@app.route('/api/cart/remove', methods=['POST'])
def remove_from_cart():
    if 'user_id' not in session: return jsonify({'success': False}), 401
    data = request.json
    user_id = str(session['user_id'])
    response = cart_table.get_item(Key={'user_id': user_id})
    current = response.get('Item', {}).get('items', [])
    updated = [i for i in current if i['id'] != data['id']]
    cart_table.put_item(Item={'user_id': user_id, 'items': updated})
    return jsonify({'success': True})

@app.route('/api/cart/clear', methods=['DELETE'])
def clear_cart():
    if 'user_id' not in session: return jsonify({'success': False}), 401
    cart_table.delete_item(Key={'user_id': str(session['user_id'])})
    return jsonify({'success': True})

@app.route('/api/products', methods=['GET'])
def get_products():
    try:
        response = products_table.scan()
        return jsonify(decimal_to_native(response.get('Items', [])))
    except: return jsonify([])

@app.route('/api/products', methods=['POST'])
def add_product():
    if session.get('role') != 'admin': return jsonify({'success': False}), 403
    data = request.json
    products_table.put_item(Item={
        'product_id': str(int(time.time()*1000)),
        'name': data['name'],
        'price': Decimal(str(data['price'])),
        'stock': int(data['stock']),
        'threshold': calculate_threshold(data['price']),
        'image_data': data.get('image')
    })
    return jsonify({'success': True})

@app.route('/api/update_stock', methods=['POST'])
def update_stock():
    if session.get('role') != 'admin': return jsonify({'success': False}), 403
    data = request.json
    products_table.update_item(Key={'product_id': str(data['id'])}, UpdateExpression="set stock = :s", ExpressionAttributeValues={':s': int(data['stock'])})
    return jsonify({'success': True})

@app.route('/api/delete_product/<id>', methods=['DELETE'])
def delete_product(id):
    if session.get('role') != 'admin': return jsonify({'success': False}), 403
    products_table.delete_item(Key={'product_id': str(id)})
    return jsonify({'success': True})

@app.route('/api/current_user')
def get_current_user():
    if 'user_id' in session:
        return jsonify({'is_logged_in': True, 'name': session.get('user_name'), 'role': session.get('role')})
    return jsonify({'is_logged_in': False})

@app.route('/api/get_users')
def get_all_users():
    if session.get('role') != "admin": return jsonify([])
    users = User.query.all()
    output = []
    for u in users:
        payment = Payment.query.filter_by(user_id=u.user_id).first()
        output.append({
            "user_id": u.user_id, "full_name": u.full_name, "email": u.email,
            "total_qty": payment.total_quantity if payment else 0,
            "total_amount": payment.total_amount if payment else 0.0
        })
    return jsonify(output)

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    role = data.get('role')
    if role == "admin":
        if email == "arora.nakul2004@gmail.com" and password == "Nakul75#":
            session['role'] = 'admin'
            return jsonify({'success': True, 'redirect': '/admin_dashboard.html'})
        return jsonify({'success': False, 'message': 'Invalid Admin Credentials'})
    user = User.query.filter_by(email=email).first()
    if user and check_password_hash(user.password_hash, password):
        session['user_id'] = user.user_id
        session['user_name'] = user.full_name
        session['role'] = 'customer'
        return jsonify({'success': True, 'redirect': '/user_dashboard.html'})
    return jsonify({'success': False, 'message': 'Invalid Credentials'})

@app.route('/api/signup', methods=['POST'])
def signup():
    data = request.get_json()
    if User.query.filter_by(email=data.get('email')).first():
        return jsonify({'success': False, 'message': 'Email exists'})
    hashed = generate_password_hash(data.get('password'))
    new_user = User(full_name=data.get('fullname'), email=data.get('email'), password_hash=hashed)
    db.session.add(new_user)
    db.session.commit()
    return jsonify({'success': True, 'redirect': '/'})

@app.route('/api/logout')
def logout():
    session.clear()
    return jsonify({'success': True})

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)
