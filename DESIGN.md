# StoreSphere - Documentation and Workflow

StoreSphere is a robust, cloud-native e-commerce platform built using Python (Flask) and deployed on AWS. It demonstrates a hybrid database architecture (SQL & NoSQL), serverless containerization (ECS Fargate), and automated cloud notifications (SNS & SES).

## 📂 1. Project Contents & Architecture

### A. Frontend Pages (/templates)

The application interface is built using **HTML5**, **CSS3**, and **JavaScript**, served dynamically via **Flask**.

- **index.html (Login Page)**: The entry point. Handles user authentication using AWS RDS verification. Redirects Admins to the Admin Dashboard and Customers to the Shop.
- **register.html (Signup Page)**: Allows new users to create an account. Data is hashed and stored in AWS RDS (users table).
- **user_dashboard.html (Shop Interface)**:
  - Fetches products dynamically from DynamoDB.
  - Displays product details, live stock status, and price.
  - Includes logic to Add/Remove items and adjust quantities (+ / -) via API calls.
- **payment.html (Checkout)**: A secure payment simulation page. It fetches the current cart state from the database, processes the transaction, and triggers backend payment recording.
- **bill.html (Invoice)**: Generates a dynamic invoice for the customer after a successful purchase. It displays the Order ID, Date, Itemized list, Tax, and Grand Total.
- **admin_dashboard.html (Inventory Management)**:
  - Allows Admins to Add, Update (Stock), or Delete products.
  - Interacts directly with DynamoDB.
  - Handles image uploads (stored as Base64/Text).
- **view_users.html (Analytics)**: An Admin-only view that pulls data from RDS (users + payments) to show registered users and their "Lifetime Value" (Total items bought and total money spent).

### B. Databases & Tables

The project utilizes a **Polyglot Persistence** architecture, using the right database for the right job.

#### 1. AWS RDS (MySQL) - Relational Data

Used for structured data requiring relationships and aggregation.

**Database Name**: `my_db`

**Table: `users`**

- `user_id` (INT, PK): Unique ID.
- `full_name` (VARCHAR): User's name.
- `email` (VARCHAR, Unique): Login credential.
- `password_hash` (VARCHAR): Securely hashed password.

**Table: `payments` (Aggregation Table)**

- `id` (INT, PK): Transaction record ID.
- `user_id` (INT, FK): Links to users table.
- `user_name` (VARCHAR): Redundant storage for faster retrieval.
- `total_quantity` (INT): Running total of items purchased by this user.
- `total_amount` (FLOAT): Running total of money spent (Lifetime Value).

#### 2. AWS DynamoDB (NoSQL) - High-Speed Data

Used for catalog management and volatile cart data.

**Table: `products`**

- `product_id` (String, Partition Key): Unique identifier.
- `name` (String): Product Name.
- `price` (Number): Unit price.
- `stock` (Number): Current inventory count.
- `threshold` (Number): The limit at which a "Low Stock" alert is triggered.
- `image_data` (String): Base64 encoded image string.

**Table: `user_cart`**

- `user_id` (String, Partition Key): The ID of the user owning the cart.
- `items` (List/Map): Stores the current shopping session {id, name, price, qty}.

### C. AWS Services Integration

- **AWS ECS (Fargate)**: Hosts the Dockerized Flask application serverless-ly.
- **AWS ECR**: Stores the Docker container images.
- **AWS SNS**: Sends email alerts to Admins when product stock drops below the defined threshold.
- **AWS SES**: Sends formatted HTML email receipts to customers upon successful payment.
- **AWS CloudWatch**: Monitors application logs for debugging app.py.

## 🔄 2. Application Workflow

### Scenario A: The Customer Journey

1.  **Registration**: The user visits `register.html`, enters details. The backend hashes the password and saves it to RDS users table.
2.  **Login**: User logs in via `index.html`. `app.py` verifies credentials against RDS. Session is started.
3.  **Shopping**:
    - User lands on `user_dashboard.html`.
    - Products are fetched via API from DynamoDB (products).
    - User adds items. These are saved instantly to DynamoDB (user_cart).
4.  **Checkout**:
    - User clicks "Make Payment".
    - `payment.html` loads the cart from the DB.
    - User confirms payment.
5.  **Transaction Processing (Backend)**:
    - **Stock Deduction**: The quantity bought is subtracted from DynamoDB (products).
    - **Payment Recording**: The backend calculates the total and updates the RDS (payments) table (adding to the user's total spend history).
    - **Cart Clearing**: The DynamoDB (user_cart) entry for that user is deleted.
    - **Receipt**: An email is sent via SES to the customer.
6.  **Invoice**: User is redirected to `bill.html` to see a summary of the transaction.

### Scenario B: The Admin Journey

1.  **Login**: Admin logs in using hardcoded secure credentials.
2.  **Dashboard**: Redirected to `admin_dashboard.html`.
3.  **Inventory Management**:
    - Admin adds a new product (Name, Price, Stock, Image).
    - System calculates a Threshold automatically based on price (e.g., expensive items have lower thresholds).
    - Data is saved to DynamoDB.
4.  **User Analytics**:
    - Admin navigates to "Users" tab (`view_users.html`).
    - Backend performs a query on RDS, merging data from users and payments.
    - Admin sees exactly how many items each user has bought and their total spending.

### Scenario C: Automated Alerts (Stock Management)

- During a customer purchase, `app.py` checks the new stock level against the product's threshold.
- **Condition**: If `current_stock < threshold`.
- **Action**: `app.py` triggers AWS SNS.
- **Result**: The Admin receives an immediate email warning: "ALERT: Low Stock for [Product Name]. Current: 5, Threshold: 15. Please restock."

## 🚀 Deployment Instructions (AWS ECS)

### Prerequisites

- AWS CLI configured.
- Docker installed.
- `requirements.txt` generated.

### Steps

1.  **Containerize**:
    - Build Docker image: `docker build -t storesphere .`
2.  **Push to ECR**:
    - Tag image: `docker tag storesphere:latest <aws_account_id>.dkr.ecr.<region>.amazonaws.com/storesphere-repo:latest`
    - Push: `docker push ...`
3.  **Deploy on ECS**:
    - Create a Task Definition in ECS (Fargate type).
    - Assign Task Role with permissions: `AmazonDynamoDBFullAccess`, `AmazonRDSFullAccess`, `AmazonSNSFullAccess`, `AmazonSESFullAccess`.
    - Create a Cluster and Run the Service.
    - Access via the Public IP on port 5000.

## 🔌 API Endpoints

- `POST /api/signup` - Create user in RDS.
- `POST /api/login` - Verify user/admin.
- `GET /api/products` - Fetch inventory from DynamoDB.
- `POST /api/cart` - Add/Update cart items in DynamoDB.
- `POST /api/record_payment` - Process transaction, update RDS, trigger SNS/SES.
- `GET /api/get_users` - Fetch user analytics for Admin.

## ⚒️ Below is the Workflow of the System

```text
+-----------------------------------------------------------------------------------+
|                          STORESPHERE CLOUD ARCHITECTURE                           |
+-----------------------------------------------------------------------------------+
|                                                                                   |
|      [ CUSTOMER ]                                         [ ADMIN ]               |
|           |                                                   |                   |
|           | 1. Register/Login                                 | 1. Login          |
|           | 2. Shop & Add to Cart                             | 2. Add Products   |
|           | 3. Checkout (Pay)                                 | 3. View Analytics |
|           v                                                   v                   |
|   +---------------------------------------------------------------------------+   |
|   |                       AWS ECS FARGATE (Compute Layer)                     |   |
|   |   +-------------------------------------------------------------------+   |   |
|   |   |                        FLASK APPLICATION                          |   |   |
|   |   |   [Routes]  [Auth Logic]  [Cart Logic]  [Payment Logic]  [Boto3]  |   |   |
|   |   +---------------------------------+---------------------------------+   |   |
|   +-------------------------------------|-------------------------------------+   |
|                                         |                                         |
|                  +----------------------+----------------------+                  |
|                  |                      |                      |                  |
|                  v                      v                      v                  |
|   +-------------------------+   +----------------+   +------------------------+   |
|   |      AWS DYNAMODB       |   |    AWS SNS     |   |        AWS RDS         |   |
|   |        (NoSQL)          |   | (Notifications)|   |        (MySQL)         |   |
|   +-------------------------+   +----------------+   +------------------------+   |
|   | > Products Table        |   |                |   | > Users Table          |   |
|   |   (Inv, Price, Stock)   |   |  IF Stock <    |   |   (Auth, Credentials)  |   |
|   |                         |   |  Threshold     |   |                        |   |
|   | > User_Cart Table       |-->|  Trigger Alert |   | > Payments Table       |   |
|   |   (Temp Cart Items)     |   |       |        |   |   (Total Spent/Qty)    |   |
|   +-------------------------+   +-------|--------+   +------------------------+   |
|                                         |                                         |
|                                         v                                         |
|                                   [ Admin Email ]                                 |
|                                                                                   |
|                                                                                   |
|   +-------------------------+                                                     |
|   |         AWS SES         |                                                     |
|   |    (Email Service)      |                                                     |
|   +-------------------------+                                                     |
|   |                         |                                                     |
|   |  On Payment Success     |                                                     |
|   |  Send Receipt/Bill      |                                                     |
|   |           |             |                                                     |
|   +-----------|-------------+                                                     |
|               |                                                                   |
|               v                                                                   |
|       [ Customer Email ]                                                          |
|                                                                                   |
+-----------------------------------------------------------------------------------+