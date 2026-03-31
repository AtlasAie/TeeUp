# TeeUp — Golf Tee Time Booking Platform
# Find and book tee times at any US course
# Affiliate model: earns commission via GolfNow/TeeOff redirects

from flask import (Flask, render_template, request, jsonify,
                   redirect, url_for, session, flash)
import sqlite3, os, requests, secrets, json
from datetime import datetime, date, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', secrets.token_hex(32))

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'teeup.db')
GOLFNOW_AFFILIATE_ID = os.getenv('GOLFNOW_AFFILIATE_ID', '')
GOOGLE_PLACES_KEY    = os.getenv('GOOGLE_PLACES_KEY', '')

# ── Database ──────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            phone TEXT,
            password_hash TEXT NOT NULL,
            handicap REAL DEFAULT 0,
            home_zip TEXT,
            home_city TEXT,
            preferred_players INTEGER DEFAULT 2,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            course_id TEXT NOT NULL,
            course_name TEXT,
            course_city TEXT,
            course_state TEXT,
            course_image TEXT,
            added_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, course_id)
        );
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            course_id TEXT,
            course_name TEXT,
            course_address TEXT,
            date TEXT NOT NULL,
            tee_time TEXT NOT NULL,
            players INTEGER NOT NULL,
            holes INTEGER DEFAULT 18,
            cart_rental INTEGER DEFAULT 0,
            club_rental INTEGER DEFAULT 0,
            price REAL,
            player_names TEXT,
            contact_name TEXT,
            contact_email TEXT,
            contact_phone TEXT,
            special_requests TEXT,
            promo_code TEXT,
            confirmation_code TEXT UNIQUE,
            affiliate_url TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location TEXT,
            lat REAL,
            lng REAL,
            date TEXT,
            players INTEGER,
            user_id INTEGER,
            results_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
    ''')
    conn.commit()
    conn.close()

init_db()

# ── Auth helpers ──────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated

def current_user():
    if not session.get('user_id'):
        return None
    conn = get_db()
    u = conn.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
    conn.close()
    return u

# ── Geocoding (Nominatim — no key needed) ────────────────────────────────────
def geocode(location):
    try:
        r = requests.get(
            'https://nominatim.openstreetmap.org/search',
            params={'q': location + ' USA', 'format': 'json', 'limit': 1},
            headers={'User-Agent': 'TeeUp/1.0 teeup.app'},
            timeout=8
        )
        data = r.json()
        if data:
            return float(data[0]['lat']), float(data[0]['lon']), data[0].get('display_name','')
    except:
        pass
    return None, None, None

# ── Tee time search (Supreme Golf API — free, aggregates GolfNow + TeeOff) ───
def search_tee_times(lat, lng, search_date, players=2, holes=18):
    try:
        r = requests.get(
            'https://services.supremegolf.com/tee-times',
            params={
                'latitude':  lat,
                'longitude': lng,
                'date':      search_date,
                'players':   players,
                'holes':     holes,
            },
            headers={
                'User-Agent': 'Mozilla/5.0',
                'Accept':     'application/json',
            },
            timeout=15
        )
        data = r.json()
        return data.get('tee_times', data) if isinstance(data, dict) else data
    except Exception as e:
        print(f"[SEARCH] API error: {e}")
        return []

def build_affiliate_url(course_name, search_date, players):
    """Build GolfNow affiliate booking URL."""
    base = 'https://www.golfnow.com/tee-times/search'
    aff  = f'?aid={GOLFNOW_AFFILIATE_ID}' if GOLFNOW_AFFILIATE_ID else '?'
    q    = f'&search={requests.utils.quote(course_name)}&date={search_date}&players={players}'
    return base + aff + q

# ── Routes: Main pages ────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html', user=current_user(),
                           today=date.today().isoformat(),
                           tomorrow=(date.today()+timedelta(days=1)).isoformat())

@app.route('/search')
def search():
    location = request.args.get('location', '').strip()
    search_date = request.args.get('date', date.today().isoformat())
    players  = int(request.args.get('players', 2))
    holes    = int(request.args.get('holes', 18))
    min_price = request.args.get('min_price', '')
    max_price = request.args.get('max_price', '')
    course_type = request.args.get('course_type', '')
    sort_by  = request.args.get('sort', 'time')

    results  = []
    error    = None
    lat, lng, display_location = None, None, location

    if location:
        lat, lng, display_location = geocode(location)
        if lat:
            raw = search_tee_times(lat, lng, search_date, players, holes)
            results = raw if isinstance(raw, list) else []
            # Log search
            try:
                conn = get_db()
                conn.execute(
                    'INSERT INTO searches (location,lat,lng,date,players,user_id,results_count) VALUES (?,?,?,?,?,?,?)',
                    (location, lat, lng, search_date, players,
                     session.get('user_id'), len(results))
                )
                conn.commit()
                conn.close()
            except: pass
        else:
            error = f'Could not find location: {location}'

    # Get user favorites for heart icons
    fav_ids = set()
    if session.get('user_id'):
        conn = get_db()
        favs = conn.execute('SELECT course_id FROM favorites WHERE user_id=?',
                            (session['user_id'],)).fetchall()
        conn.close()
        fav_ids = {f['course_id'] for f in favs}

    return render_template('results.html',
        user=current_user(),
        results=results,
        location=location,
        display_location=display_location,
        search_date=search_date,
        players=players,
        holes=holes,
        sort_by=sort_by,
        fav_ids=fav_ids,
        error=error,
        result_count=len(results),
        today=date.today().isoformat(),
    )

@app.route('/course/<course_id>')
def course_detail(course_id):
    location  = request.args.get('location', '')
    search_date = request.args.get('date', date.today().isoformat())
    players   = int(request.args.get('players', 2))
    course_name = request.args.get('name', '')
    is_fav = False
    if session.get('user_id'):
        conn = get_db()
        row = conn.execute('SELECT id FROM favorites WHERE user_id=? AND course_id=?',
                           (session['user_id'], course_id)).fetchone()
        conn.close()
        is_fav = row is not None
    return render_template('course.html',
        user=current_user(),
        course_id=course_id,
        course_name=course_name,
        search_date=search_date,
        players=players,
        location=location,
        is_fav=is_fav,
        today=date.today().isoformat(),
    )

@app.route('/book/<course_id>', methods=['GET', 'POST'])
def book(course_id):
    course_name  = request.args.get('name', request.form.get('course_name', ''))
    tee_time     = request.args.get('time', request.form.get('tee_time', ''))
    search_date  = request.args.get('date', request.form.get('date', date.today().isoformat()))
    players      = int(request.args.get('players', request.form.get('players', 2)))
    price        = request.args.get('price', request.form.get('price', '0'))
    cart_included = request.args.get('cart', '0')

    user = current_user()

    if request.method == 'POST':
        # Collect form data
        contact_name  = request.form.get('contact_name', '').strip()
        contact_email = request.form.get('contact_email', '').strip()
        contact_phone = request.form.get('contact_phone', '').strip()
        player_names  = request.form.getlist('player_name')
        cart_rental   = 1 if request.form.get('cart_rental') else 0
        club_rental   = 1 if request.form.get('club_rental') else 0
        special_req   = request.form.get('special_requests', '').strip()
        promo         = request.form.get('promo_code', '').strip()
        holes         = int(request.form.get('holes', 18))

        if not contact_name or not contact_email:
            flash('Name and email are required.', 'error')
        else:
            code = secrets.token_urlsafe(8).upper()
            aff_url = build_affiliate_url(course_name, search_date, players)
            conn = get_db()
            conn.execute('''
                INSERT INTO bookings
                (user_id, course_id, course_name, date, tee_time, players, holes,
                 cart_rental, club_rental, price, player_names, contact_name,
                 contact_email, contact_phone, special_requests, promo_code,
                 confirmation_code, affiliate_url, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ''', (
                session.get('user_id'), course_id, course_name, search_date,
                tee_time, players, holes, cart_rental, club_rental, price,
                json.dumps(player_names), contact_name, contact_email,
                contact_phone, special_req, promo, code, aff_url, 'confirmed'
            ))
            conn.commit()
            conn.close()
            return redirect(url_for('confirmation', code=code))

    return render_template('book.html',
        user=user,
        course_id=course_id,
        course_name=course_name,
        tee_time=tee_time,
        search_date=search_date,
        players=players,
        price=price,
        cart_included=cart_included,
        range=range,
    )

@app.route('/confirmation/<code>')
def confirmation(code):
    conn = get_db()
    booking = conn.execute('SELECT * FROM bookings WHERE confirmation_code=?',
                           (code,)).fetchone()
    conn.close()
    if not booking:
        return redirect(url_for('index'))
    player_names = json.loads(booking['player_names']) if booking['player_names'] else []
    return render_template('confirmation.html',
        user=current_user(),
        booking=booking,
        player_names=player_names,
    )

# ── Auth ──────────────────────────────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user_id'):
        return redirect(url_for('index'))
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password_hash'], password):
            session['user_id']   = user['id']
            session['user_name'] = user['name']
            return redirect(request.args.get('next') or url_for('index'))
        flash('Invalid email or password.', 'error')
    return render_template('auth.html', mode='login', user=None)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if session.get('user_id'):
        return redirect(url_for('index'))
    if request.method == 'POST':
        name     = request.form.get('name', '').strip()
        email    = request.form.get('email', '').strip().lower()
        phone    = request.form.get('phone', '').strip()
        password = request.form.get('password', '')
        home_zip = request.form.get('home_zip', '').strip()
        handicap = request.form.get('handicap', '0')
        if not name or not email or not password:
            flash('Name, email, and password are required.', 'error')
        elif len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
        else:
            try:
                conn = get_db()
                conn.execute(
                    'INSERT INTO users (email,name,phone,password_hash,home_zip,handicap) VALUES (?,?,?,?,?,?)',
                    (email, name, phone, generate_password_hash(password), home_zip, handicap)
                )
                conn.commit()
                user = conn.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
                conn.close()
                session['user_id']   = user['id']
                session['user_name'] = user['name']
                flash(f'Welcome to TeeUp, {name}!', 'success')
                return redirect(url_for('index'))
            except Exception as e:
                if 'UNIQUE' in str(e):
                    flash('An account with that email already exists.', 'error')
                else:
                    flash('Error creating account. Please try again.', 'error')
    return render_template('auth.html', mode='register', user=None)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# ── Account ───────────────────────────────────────────────────────────────────
@app.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    user = current_user()
    if request.method == 'POST':
        name     = request.form.get('name', '').strip()
        phone    = request.form.get('phone', '').strip()
        home_zip = request.form.get('home_zip', '').strip()
        home_city = request.form.get('home_city', '').strip()
        handicap = request.form.get('handicap', '0')
        preferred_players = request.form.get('preferred_players', '2')
        conn = get_db()
        conn.execute(
            'UPDATE users SET name=?,phone=?,home_zip=?,home_city=?,handicap=?,preferred_players=? WHERE id=?',
            (name, phone, home_zip, home_city, handicap, preferred_players, user['id'])
        )
        conn.commit()
        conn.close()
        session['user_name'] = name
        flash('Profile updated.', 'success')
        return redirect(url_for('account'))
    return render_template('account.html', user=user)

@app.route('/favorites')
@login_required
def favorites():
    conn = get_db()
    favs = conn.execute(
        'SELECT * FROM favorites WHERE user_id=? ORDER BY added_at DESC',
        (session['user_id'],)
    ).fetchall()
    conn.close()
    return render_template('favorites.html', user=current_user(), favorites=favs)

@app.route('/bookings')
@login_required
def bookings():
    conn = get_db()
    bks = conn.execute(
        'SELECT * FROM bookings WHERE user_id=? ORDER BY created_at DESC',
        (session['user_id'],)
    ).fetchall()
    conn.close()
    return render_template('bookings.html', user=current_user(), bookings=bks)

# ── API endpoints ─────────────────────────────────────────────────────────────
@app.route('/api/favorite', methods=['POST'])
@login_required
def api_favorite():
    data = request.json or {}
    course_id   = data.get('course_id', '')
    course_name = data.get('course_name', '')
    course_city = data.get('course_city', '')
    course_state = data.get('course_state', '')
    course_image = data.get('course_image', '')
    conn = get_db()
    existing = conn.execute(
        'SELECT id FROM favorites WHERE user_id=? AND course_id=?',
        (session['user_id'], course_id)
    ).fetchone()
    if existing:
        conn.execute('DELETE FROM favorites WHERE user_id=? AND course_id=?',
                     (session['user_id'], course_id))
        conn.commit()
        conn.close()
        return jsonify({'favorited': False})
    else:
        conn.execute(
            'INSERT INTO favorites (user_id,course_id,course_name,course_city,course_state,course_image) VALUES (?,?,?,?,?,?)',
            (session['user_id'], course_id, course_name, course_city, course_state, course_image)
        )
        conn.commit()
        conn.close()
        return jsonify({'favorited': True})

@app.route('/api/geocode')
def api_geocode():
    q = request.args.get('q', '')
    if not q:
        return jsonify([])
    try:
        r = requests.get(
            'https://nominatim.openstreetmap.org/search',
            params={'q': q + ' USA', 'format': 'json', 'limit': 5,
                    'addressdetails': 1, 'countrycodes': 'us'},
            headers={'User-Agent': 'TeeUp/1.0'},
            timeout=5
        )
        results = []
        for item in r.json():
            addr = item.get('address', {})
            city  = addr.get('city') or addr.get('town') or addr.get('village', '')
            state = addr.get('state', '')
            label = f"{city}, {state}" if city and state else item['display_name'].split(',')[0]
            results.append({'label': label, 'lat': item['lat'], 'lng': item['lon']})
        return jsonify(results)
    except:
        return jsonify([])

@app.route('/api/search')
def api_search():
    lat     = request.args.get('lat', type=float)
    lng     = request.args.get('lng', type=float)
    d       = request.args.get('date', date.today().isoformat())
    players = request.args.get('players', 2, type=int)
    holes   = request.args.get('holes', 18, type=int)
    if not lat or not lng:
        return jsonify({'error': 'lat/lng required'}), 400
    results = search_tee_times(lat, lng, d, players, holes)
    return jsonify(results)

@app.route('/about')
def about():
    return render_template('static_page.html', user=current_user(),
        title='About TeeUp',
        heading='About TeeUp',
        body='''TeeUp is a free golf tee time search and booking platform covering
        15,000+ courses across all 50 states. Search, compare prices, and book
        instantly with zero booking fees. Built for golfers, by golfers.''')

@app.route('/privacy')
def privacy():
    return render_template('static_page.html', user=current_user(),
        title='Privacy Policy',
        heading='Privacy Policy',
        body='''TeeUp collects only the information necessary to process your bookings —
        your name, email, and tee time preferences. We never sell your data to third parties.
        Bookings are processed securely and your payment information is never stored on our servers.
        For questions contact hello@teeup.app.''')

@app.route('/terms')
def terms():
    return render_template('static_page.html', user=current_user(),
        title='Terms of Service',
        heading='Terms of Service',
        body='''By using TeeUp you agree to book tee times for personal use only.
        TeeUp earns a referral fee when bookings are completed through our affiliate partners.
        Cancellation policies are set by each individual golf course.
        TeeUp is not responsible for course closures, weather cancellations, or changes made by the course.
        For questions contact hello@teeup.app.''')

@app.errorhandler(404)
def not_found(e):
    return render_template('static_page.html', user=current_user(),
        title='Page Not Found',
        heading='Page not found',
        body='The page you\'re looking for doesn\'t exist.'), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('static_page.html', user=current_user(),
        title='Something went wrong',
        heading='Something went wrong',
        body='An error occurred. Please try again or go back to the homepage.'), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=True)
