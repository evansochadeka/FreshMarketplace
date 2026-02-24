# Fresh Marketplace - Python Flask Application

A simple marketplace app for buying and selling fresh fruits and vegetables with delivery tracking.

## Features

- **Buyer Portal**: Browse products, add to cart, place orders, track deliveries
- **Rider Dashboard**: View assigned deliveries, update delivery status
- **Admin Panel**: Manage products, users, and orders
- **Review System**: Users can review each other
- **Auto-assignment**: Riders are automatically assigned to nearby deliveries

## Demo Accounts

- **Admin**: username=`admin`, password=`admin123`
- **Rider**: username=`rider1`, password=`rider123`
- **New Users**: Register as buyer or rider

## Local Development

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

2. Run the app:
```bash
python app.py
```

3. Visit `http://localhost:5000`

## Deploy to Render.com

### Quick Deploy (Recommended)

1. Push this code to GitHub
2. Go to [Render.com](https://render.com) and sign in
3. Click "New +" → "Web Service"
4. Connect your GitHub repository
5. Render will auto-detect the `render.yaml` and set up:
   - Python web service
   - PostgreSQL database
   - Environment variables
6. Click "Create Web Service"

### Manual Deploy

1. Create a new Web Service on Render
2. Set build command: `pip install -r requirements.txt`
3. Set start command: `gunicorn app:app`
4. Add environment variables:
   - `SECRET_KEY`: (generate a random string)
   - `DATABASE_URL`: (from your Render PostgreSQL database)
5. Deploy!

## Database Setup

The app will automatically:
- Create all tables on first run
- Seed with sample products
- Create default admin and rider accounts

## Tech Stack

- **Backend**: Python Flask
- **Database**: PostgreSQL (SQLAlchemy ORM)
- **Frontend**: Bootstrap 5
- **Deployment**: Render.com (optimized)

## Project Structure

```
├── app.py                 # Main Flask application
├── requirements.txt       # Python dependencies
├── render.yaml           # Render deployment config
├── templates/            # HTML templates
│   ├── base.html
│   ├── index.html
│   ├── products.html
│   └── ...
└── static/
    └── css/
        └── style.css     # Custom styles
```

## Environment Variables

- `DATABASE_URL`: PostgreSQL connection string
- `SECRET_KEY`: Flask secret key for sessions
- `PORT`: Port to run the app (default: 5000)
- `FLASK_ENV`: Set to `development` for debug mode

## Production Notes

- Uses `gunicorn` as the production WSGI server
- PostgreSQL for production database
- Automatic HTTPS on Render
- Auto-deploys on git push (if configured)
