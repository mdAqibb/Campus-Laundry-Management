from flask import Flask, render_template, request, redirect, url_for, flash, session, g
import sqlite3
from datetime import datetime
from contextlib import contextmanager
from typing import Generator

app = Flask(__name__)
app.secret_key = 'your_secret_key'

DATABASE = 'laundry.db'

def get_db():
    """Get database connection for the current request context"""
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE, timeout=20)
        g.db.row_factory = sqlite3.Row
    return g.db

def close_db(e=None):
    """Close database connection at the end of request"""
    db = g.pop('db', None)
    if db is not None:
        db.close()

@app.teardown_appcontext
def teardown_db(exception):
    close_db()

@contextmanager
def get_db_cursor() -> Generator[sqlite3.Cursor, None, None]:
    """Context manager for database operations"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()

def init_db():
    """Initialize the database"""
    # Create a direct database connection for initialization
    conn = sqlite3.connect(DATABASE, timeout=20)
    c = conn.cursor()
    try:
        # Students table
        c.execute('''CREATE TABLE IF NOT EXISTS students
                 (id INTEGER PRIMARY KEY, 
                  full_name TEXT UNIQUE, 
                  password TEXT,
                  room_number TEXT,
                  gender TEXT
                  )''')
                  
        # Laundry table with bag limit tracking
        c.execute('''CREATE TABLE IF NOT EXISTS laundry
                 (id INTEGER PRIMARY KEY, 
                  student_id INTEGER,
                  status TEXT,
                  date_submitted TIMESTAMP,
                  notification_sent BOOLEAN DEFAULT 0,
                  FOREIGN KEY (student_id) REFERENCES students (id))''')
                  
        # Complaints table
        c.execute('''CREATE TABLE IF NOT EXISTS complaints
                 (id INTEGER PRIMARY KEY,
                  student_id INTEGER,
                  laundry_id INTEGER,
                  description TEXT,
                  status TEXT DEFAULT 'pending',
                  date_submitted TIMESTAMP,
                  admin_response TEXT,
                  date_resolved TIMESTAMP,
                  FOREIGN KEY (student_id) REFERENCES students (id),
                  FOREIGN KEY (laundry_id) REFERENCES laundry (id))''')
                  
        # Notifications table
        c.execute('''CREATE TABLE IF NOT EXISTS notifications
                 (id INTEGER PRIMARY KEY,
                  student_id INTEGER,
                  message TEXT,
                  date_created TIMESTAMP,
                  is_read BOOLEAN DEFAULT 0,
                  FOREIGN KEY (student_id) REFERENCES students (id))''')
        conn.commit()
    except Exception as e:
        print(f"Error initializing database: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

# Initialize database
with app.app_context():
    init_db()

def count_active_laundry(student_id):
    with get_db_cursor() as c:
        c.execute('''SELECT COUNT(*) FROM laundry 
                    WHERE student_id = ? 
                    AND status NOT IN ('collected', 'complete')''', (student_id,))
        return c.fetchone()[0]

app.jinja_env.globals.update(count_active_laundry=count_active_laundry)

def create_notification(student_id, message):
    with get_db_cursor() as c:
        c.execute('''INSERT INTO notifications (student_id, message, date_created)
                    VALUES (?, ?, ?)''', (student_id, message, datetime.now()))

@app.route('/')
def home():
    if 'user_id' in session:
        if session.get('is_admin'):
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('student_dashboard'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        full_name = request.form['full_name']
        password = request.form['password']
        
        if full_name == 'admin' and password == 'admin123':
            session['is_admin'] = True
            return redirect(url_for('admin_dashboard'))
        
        try:
            with get_db_cursor() as c:
                c.execute('SELECT * FROM students WHERE full_name = ? AND password = ?', 
                         (full_name, password))
                student = c.fetchone()
                
                if student:
                    session['user_id'] = student[0]
                    return redirect(url_for('student_dashboard'))
                else:
                    flash('Invalid credentials!')
        except sqlite3.Error as e:
            flash(f'Database error: {str(e)}')
            
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        full_name = request.form['full_name']
        password = request.form['password']
        room_number = request.form['room_number']
        gender = request.form['gender']
        
        try:
            with get_db_cursor() as c:
                c.execute('''INSERT INTO students 
                            (full_name, password, room_number, gender) 
                            VALUES (?, ?, ?, ?)''',
                         (full_name, password, room_number, gender))
                flash('Registration successful! Please login.')
                return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Student already registered!')
        except sqlite3.Error as e:
            flash(f'Database error: {str(e)}')
            
    return render_template('register.html')

@app.route('/student/dashboard')
def student_dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    student_id = session['user_id']
    
    try:
        with get_db_cursor() as c:
            # Get student info
            c.execute('SELECT * FROM students WHERE id = ?', (student_id,))
            student = c.fetchone()
            
            # Get laundry items
            c.execute('''SELECT * FROM laundry 
                        WHERE student_id = ? 
                        ORDER BY date_submitted DESC''', (student_id,))
            laundry_items = c.fetchall()
            
            # Get unread notifications
            c.execute('''SELECT * FROM notifications 
                        WHERE student_id = ? AND is_read = 0 
                        ORDER BY date_created DESC''', (student_id,))
            notifications = c.fetchall()
            
            # Get complaints
            c.execute('''SELECT * FROM complaints 
                        WHERE student_id = ? 
                        ORDER BY date_submitted DESC''', (student_id,))
            complaints = c.fetchall()
            
            return render_template('student_dashboard.html',
                                student=student,
                                laundry_items=laundry_items,
                                notifications=notifications,
                                complaints=complaints)
    except sqlite3.Error as e:
        flash(f'Database error: {str(e)}')
        return redirect(url_for('home'))

@app.route('/student/submit_laundry', methods=['POST'])
def submit_laundry():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    student_id = session['user_id']
    active_count = count_active_laundry(student_id)
    
    if active_count >= 2:
        flash('You have reached the maximum limit of active laundry bags!')
        return redirect(url_for('student_dashboard'))
    
    try:
        with get_db_cursor() as c:
            c.execute('''INSERT INTO laundry (student_id, status, date_submitted)
                        VALUES (?, 'pending', ?)''', (student_id, datetime.now()))
            flash('Laundry submitted successfully!')
    except sqlite3.Error as e:
        flash(f'Error submitting laundry: {str(e)}')
        
    return redirect(url_for('student_dashboard'))

@app.route('/student/submit_complaint', methods=['POST'])
def submit_complaint():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    student_id = session['user_id']
    laundry_id = request.form['laundry_id']
    description = request.form['description']
    
    try:
        with get_db_cursor() as c:
            c.execute('''INSERT INTO complaints 
                        (student_id, laundry_id, description, date_submitted)
                        VALUES (?, ?, ?, ?)''',
                     (student_id, laundry_id, description, datetime.now()))
            flash('Complaint submitted successfully!')
    except sqlite3.Error as e:
        flash(f'Error submitting complaint: {str(e)}')
        
    return redirect(url_for('student_dashboard'))

@app.route('/mark_notification_read/<int:notification_id>')
def mark_notification_read(notification_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    try:
        with get_db_cursor() as c:
            c.execute('UPDATE notifications SET is_read = 1 WHERE id = ?', 
                     (notification_id,))
    except sqlite3.Error as e:
        flash(f'Error marking notification as read: {str(e)}')
        
    return redirect(url_for('student_dashboard'))

@app.route('/admin/dashboard')
def admin_dashboard():
    if 'is_admin' not in session:
        return redirect(url_for('login'))
    
    search_query = request.args.get('search', '')
    
    try:
        with get_db_cursor() as c:
            # Get laundry items with student info
            if search_query:
                c.execute('''SELECT l.*, s.full_name, s.room_number, s.gender 
                            FROM laundry l
                            JOIN students s ON l.student_id = s.id
                            WHERE s.full_name LIKE ?
                            ORDER BY l.date_submitted DESC''', 
                         ('%' + search_query + '%',))
            else:
                c.execute('''SELECT l.*, s.full_name, s.room_number, s.gender 
                            FROM laundry l
                            JOIN students s ON l.student_id = s.id
                            ORDER BY l.date_submitted DESC''')
            laundry_items = c.fetchall()
            
            # Get all complaints
            c.execute('''SELECT c.*, s.full_name, l.date_submitted as laundry_date
                        FROM complaints c
                        JOIN students s ON c.student_id = s.id
                        JOIN laundry l ON c.laundry_id = l.id
                        ORDER BY c.date_submitted DESC''')
            complaints = c.fetchall()
            
            return render_template('admin_dashboard.html',
                                laundry_items=laundry_items,
                                complaints=complaints,
                                search_query=search_query)
    except sqlite3.Error as e:
        flash(f'Database error: {str(e)}')
        return redirect(url_for('home'))

@app.route('/admin/update_status/<int:laundry_id>', methods=['POST'])
def update_status(laundry_id):
    if 'is_admin' not in session:
        return redirect(url_for('login'))
    
    new_status = request.form['status']
    
    try:
        with get_db_cursor() as c:
            # Update laundry status
            c.execute('UPDATE laundry SET status = ? WHERE id = ?', 
                     (new_status, laundry_id))
            
            # If status is complete, create notification
            if new_status == 'complete':
                c.execute('SELECT student_id FROM laundry WHERE id = ?', (laundry_id,))
                student_id = c.fetchone()[0]
                create_notification(student_id, 
                                 'Your laundry is ready for collection!')
            
            flash(f'Status updated to {new_status}!')
    except sqlite3.Error as e:
        flash(f'Error updating status: {str(e)}')
        
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/resolve_complaint/<int:complaint_id>', methods=['POST'])
def resolve_complaint(complaint_id):
    if 'is_admin' not in session:
        return redirect(url_for('login'))
    
    response = request.form['response']
    
    try:
        with get_db_cursor() as c:
            c.execute('''UPDATE complaints 
                        SET status = 'resolved',
                            admin_response = ?,
                            date_resolved = ?
                        WHERE id = ?''',
                     (response, datetime.now(), complaint_id))
            
            # Create notification for student
            c.execute('SELECT student_id FROM complaints WHERE id = ?', (complaint_id,))
            student_id = c.fetchone()[0]
            create_notification(student_id, 
                             f'Your complaint has been resolved. Response: {response}')
            
            flash('Complaint resolved successfully!')
    except sqlite3.Error as e:
        flash(f'Error resolving complaint: {str(e)}')
        
    return redirect(url_for('admin_dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(debug=True)