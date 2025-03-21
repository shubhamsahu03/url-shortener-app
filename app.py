import streamlit as st
import psycopg2
import random
import string
import validators
from urllib.parse import urlparse
import time
import datetime
import qrcode
from io import BytesIO
import base64

def get_base_url():
    # Always use localhost:8501
    return "http://localhost:8501/"

# Use localhost:8501 as the base URL
base_url = get_base_url()

# Database connection function with support for DATABASE_URL
def get_db_connection():
    # Add retry logic for better reliability
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            # Check if DATABASE_URL is provided in secrets
            if "DATABASE_URL" in st.secrets:
                # Connect using the full URL
                conn = psycopg2.connect(st.secrets["DATABASE_URL"])
            else:
                # Connect using individual parameters
                conn = psycopg2.connect(
                    host=st.secrets["db_host"],
                    database=st.secrets["db_name"],
                    user=st.secrets["db_user"],
                    password=st.secrets["db_password"],
                    port=st.secrets["db_port"],
                    sslmode='require',
                    connect_timeout=10
                )
            return conn
        except psycopg2.OperationalError as e:
            retry_count += 1
            if retry_count >= max_retries:
                st.error(f"Database connection failed after {max_retries} attempts: {e}")
                raise
            time.sleep(2)  # Wait before retrying

# Function to initialize the database with enhanced schema
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Create table with additional features
    cur.execute('''
        CREATE TABLE IF NOT EXISTS shortened_urls (
            id SERIAL PRIMARY KEY,
            original_url TEXT NOT NULL,
            short_code VARCHAR(10) UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            access_count INTEGER DEFAULT 0,
            last_accessed TIMESTAMP,
            created_by TEXT,
            is_custom BOOLEAN DEFAULT FALSE,
            notes TEXT
        );
        
        CREATE INDEX IF NOT EXISTS idx_short_code ON shortened_urls(short_code);
        CREATE INDEX IF NOT EXISTS idx_created_at ON shortened_urls(created_at);
    ''')
    
    conn.commit()
    cur.close()
    conn.close()

# Function to generate a random short code
def generate_short_code(length=6):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

# Function to add a new URL to the database with enhanced options
def add_url(original_url, custom_code=None, expiry_days=None, notes=None, creator=None):
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Handle custom code
    is_custom = False
    if custom_code and custom_code.strip():
        short_code = custom_code.strip()
        is_custom = True
        
        # Check if custom code already exists
        cur.execute("SELECT id FROM shortened_urls WHERE short_code = %s", (short_code,))
        if cur.fetchone():
            cur.close()
            conn.close()
            return None, "Custom code already in use"
    else:
        # Check if URL already exists (only for non-custom codes)
        cur.execute("SELECT short_code FROM shortened_urls WHERE original_url = %s AND is_custom = FALSE", (original_url,))
        result = cur.fetchone()
        
        if result:
            short_code = result[0]
        else:
            # Generate a unique short code
            while True:
                short_code = generate_short_code()
                cur.execute("SELECT id FROM shortened_urls WHERE short_code = %s", (short_code,))
                if not cur.fetchone():
                    break
    
    # Handle expiry date
    expires_at = None
    if expiry_days and expiry_days > 0:
        expires_at = datetime.datetime.now() + datetime.timedelta(days=expiry_days)
    
    try:
        # Insert the new URL with all options
        cur.execute(
            """
            INSERT INTO shortened_urls 
            (original_url, short_code, expires_at, notes, created_by, is_custom) 
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (original_url, short_code, expires_at, notes, creator, is_custom)
        )
        conn.commit()
        return short_code, None
    except Exception as e:
        conn.rollback()
        return None, str(e)
    finally:
        cur.close()
        conn.close()

# Function to get original URL from short code with expiration check
def get_original_url(short_code):
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT original_url, expires_at 
        FROM shortened_urls 
        WHERE short_code = %s
    """, (short_code,))
    result = cur.fetchone()
    
    if not result:
        cur.close()
        conn.close()
        return None, "Short code not found"
    
    original_url, expires_at = result
    
    # Check if URL has expired
    if expires_at and expires_at < datetime.datetime.now():
        cur.close()
        conn.close()
        return None, "This short URL has expired"
    
    # Update access count and last accessed timestamp
    cur.execute("""
        UPDATE shortened_urls 
        SET access_count = access_count + 1, last_accessed = CURRENT_TIMESTAMP 
        WHERE short_code = %s
    """, (short_code,))
    conn.commit()
    
    cur.close()
    conn.close()
    
    return original_url, None

# Function to generate QR code for a URL
def generate_qr_code(url):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()

# Function to get detailed statistics
def get_url_stats():
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT 
            short_code, 
            original_url, 
            access_count, 
            created_at, 
            last_accessed,
            expires_at,
            is_custom,
            notes
        FROM shortened_urls
        ORDER BY access_count DESC
        LIMIT 20
    """)
    stats = cur.fetchall()
    
    # Get overall statistics
    cur.execute("""
        SELECT 
            COUNT(*) as total_urls,
            SUM(access_count) as total_clicks,
            MAX(access_count) as max_clicks,
            AVG(access_count) as avg_clicks
        FROM shortened_urls
    """)
    overall_stats = cur.fetchone()
    
    cur.close()
    conn.close()
    
    return stats, overall_stats

# Function to delete a shortened URL
def delete_url(short_code):
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("DELETE FROM shortened_urls WHERE short_code = %s", (short_code,))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        return False
    finally:
        cur.close()
        conn.close()

# Custom CSS for better UI
def load_css():
    st.markdown("""
    <style>
    .url-card {
        padding: 1.5rem;
        border-radius: 0.5rem;
        background-color: #f8f9fa;
        margin: 1rem 0;
        border-left: 5px solid #4CAF50;
    }
    .stat-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #e9ecef;
        margin-bottom: 0.5rem;
        text-align: center;
    }
    .expired {
        border-left: 5px solid #dc3545 !important;
        opacity: 0.7;
    }
    .custom-url {
        border-left: 5px solid #007bff !important;
    }
    .stats-header {
        text-align: center;
        margin: 1rem 0;
    }
    .copy-btn {
        background-color: #4CAF50;
        border: none;
        color: white;
        padding: 8px 16px;
        text-align: center;
        text-decoration: none;
        display: inline-block;
        font-size: 14px;
        margin: 4px 2px;
        cursor: pointer;
        border-radius: 4px;
        transition: background-color 0.3s;
    }
    .copy-btn:hover {
        background-color: #45a049;
    }
    .copy-input-container {
        display: flex;
        width: 100%;
        margin: 10px 0;
    }
    .copy-input {
        flex-grow: 1;
        padding: 8px;
        border: 1px solid #ccc;
        border-radius: 4px 0 0 4px;
    }
    .copy-container-btn {
        border-radius: 0 4px 4px 0;
        margin: 0;
    }
    .copied-message {
        color: #4CAF50;
        font-size: 14px;
        margin-left: 10px;
        display: none;
    }
    .copied-message.show {
        display: inline;
    }
    </style>
    """, unsafe_allow_html=True)

# Add JavaScript for copy to clipboard functionality
def add_copy_script():
    st.markdown("""
    <script>
    function copyToClipboard(elementId) {
        const textField = document.getElementById(elementId);
        textField.select();
        textField.setSelectionRange(0, 99999);
        
        navigator.clipboard.writeText(textField.value)
            .then(() => {
                const msgId = elementId + '-msg';
                const msg = document.getElementById(msgId);
                msg.classList.add('show');
                
                setTimeout(() => {
                    msg.classList.remove('show');
                }, 2000);
            })
            .catch(err => {
                console.error('Could not copy text: ', err);
            });
    }
    </script>
    """, unsafe_allow_html=True)

# Main Streamlit app
def main():
    # Check for redirection first before setting up the main app
    # Get query parameters using the new API
    query_params = st.query_params
    
    # Check for code parameter - if present, handle redirection before setting up the main app
    if "code" in query_params:
        code = query_params["code"]
        original_url, error = get_original_url(code)
        
        if error:
            # Set minimal page config for error
            st.set_page_config(
                page_title="URL Error",
                page_icon="❌",
                layout="centered",
                initial_sidebar_state="collapsed"
            )
            st.error(error)
            st.markdown("<div style='text-align: center;'><a href='/'>Back to URL Shortener</a></div>", unsafe_allow_html=True)
            return  # Exit function to prevent the rest of the app from loading
        else:
            # Set minimal page config for redirect
            st.set_page_config(
                page_title="Redirecting...",
                page_icon="🔄",
                layout="centered",
                initial_sidebar_state="collapsed"
            )
            
            # Hide all other elements
            st.markdown("""
                <style>
                    #MainMenu {visibility: hidden;}
                    footer {visibility: hidden;}
                    .stTabs {display: none;}
                    header {visibility: hidden;}
                    .block-container {padding-top: 2rem;}
                </style>
            """, unsafe_allow_html=True)
            
            # Show only the redirection message
            st.markdown("<h2 style='text-align: center;'>Redirecting to long URL...</h2>", unsafe_allow_html=True)
            
            # Add the actual redirection
            st.markdown(f'<meta http-equiv="refresh" content="1;URL=\'{original_url}\'">', unsafe_allow_html=True)
            
            # Add a fallback link
            st.markdown(f"<div style='text-align: center;'><a href='{original_url}'>Click here if not redirected automatically</a></div>", unsafe_allow_html=True)
            
            # Exit function to prevent the rest of the app from loading
            return
    
    # If we get here, this is not a redirection, so set up the main app
    st.set_page_config(
        page_title="Advanced URL Shortener", 
        page_icon="🔗", 
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    # Load custom CSS
    load_css()
    
    # Add JavaScript for copy functionality
    add_copy_script()
    
    # Initialize database
    init_db()
    
    # Sidebar for app info and options
    with st.sidebar:
        st.title("🔗 URL Shortener")
        st.markdown("Create and manage short links easily!")
        
        st.header("About")
        st.markdown("""
        This application allows you to:
        - Create short URLs with custom codes
        - Set expiration dates for links
        - Track usage statistics
        - Generate QR codes for your links
        """)
        
        # Using localhost:8501
        st.info(f"Using domain: {base_url}")
        
    # Define tabs for different functionalities
    tab1, tab2, tab3, tab4 = st.tabs(["Create Short URL", "Expand URL", "Statistics", "Manage URLs"])
    
    # Tab 1: Create Short URL
    with tab1:
        st.header("Create a Short URL")
        
        # URL input and options
        col1, col2 = st.columns([2, 1])
        
        with col1:
            original_url = st.text_input("Enter the URL to shorten:", key="shorten_url")
            custom_code = st.text_input("Custom code (optional):", 
                                       help="Leave blank for auto-generated code")
            
        with col2:
            expiry_options = {
                "Never": None,
                "1 day": 1,
                "7 days": 7,
                "30 days": 30,
                "90 days": 90,
                "Custom": -1
            }
            expiry_selection = st.selectbox("Expiration:", options=list(expiry_options.keys()))
            
            expiry_days = expiry_options[expiry_selection]
            if expiry_selection == "Custom":
                expiry_days = st.number_input("Number of days:", min_value=1, value=14)
        
        notes = st.text_area("Notes (optional):", 
                           help="Add any notes or description for this URL")
        
        creator = st.text_input("Your name/identifier (optional):", 
                              help="Track who created this link")
        
        # Create button
        if st.button("📝 Create Short URL"):
            if not original_url:
                st.error("Please enter a URL")
            elif not validators.url(original_url):
                st.error("Please enter a valid URL")
            else:
                short_code, error = add_url(
                    original_url, 
                    custom_code, 
                    expiry_days, 
                    notes, 
                    creator
                )
                
                if error:
                    st.error(f"Error: {error}")
                else:
                    # Create shortened URL with code parameter for redirection
                    shortened_url = f"{base_url}?code={short_code}"
                    
                    st.success("URL shortened successfully!")
                    
                    # Display the result in a nice card with copy button
                    st.markdown(f"""
                    <div class="url-card">
                        <h3>Your shortened URL:</h3>
                        <div class="copy-input-container">
                            <input type="text" id="shortened-url" class="copy-input" value="{shortened_url}" readonly>
                            <button onclick="copyToClipboard('shortened-url')" class="copy-btn copy-container-btn">Copy</button>
                            <span id="shortened-url-msg" class="copied-message">Copied!</span>
                        </div>
                        <p><strong>Short code:</strong> {short_code}</p>
                        <p><strong>Expires:</strong> {f"After {expiry_days} days" if expiry_days else "Never"}</p>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # Generate QR code
                    qr_col1, qr_col2 = st.columns([1, 2])
                    with qr_col1:
                        qr_img = generate_qr_code(shortened_url)
                        st.markdown(f"""
                        <div style="text-align:center">
                            <h4>QR Code:</h4>
                            <img src="data:image/png;base64,{qr_img}" width="200">
                            <p><a href="data:image/png;base64,{qr_img}" download="qrcode.png">Download QR Code</a></p>
                        </div>
                        """, unsafe_allow_html=True)
                    
                    with qr_col2:
                        st.markdown("""
                        <div style="padding:20px">
                            <h4>What's Next?</h4>
                            <ul>
                                <li>Copy and share your shortened URL</li>
                                <li>Use the QR code for print materials</li>
                                <li>Track clicks in the Statistics tab</li>
                            </ul>
                        </div>
                        """, unsafe_allow_html=True)
    
    # Tab 2: Expand URL
    with tab2:
        st.header("Expand a Short URL")
        
        short_code_input = st.text_input("Enter the short code:", key="expand_url")
        
        if st.button("🔍 Expand URL"):
            if not short_code_input:
                st.error("Please enter a short code")
            else:
                original_url, error = get_original_url(short_code_input)
                
                if error:
                    st.error(error)
                else:
                    st.success("URL found!")
                    
                    # Display original URL with copy button
                    st.markdown(f"""
                    <div class="url-card">
                        <h3>Original URL:</h3>
                        <div class="copy-input-container">
                            <input type="text" id="original-url" class="copy-input" value="{original_url}" readonly>
                            <button onclick="copyToClipboard('original-url')" class="copy-btn copy-container-btn">Copy</button>
                            <span id="original-url-msg" class="copied-message">Copied!</span>
                        </div>
                        <a href="{original_url}" target="_blank" style="margin-top: 10px; display: inline-block;">Open URL →</a>
                    </div>
                    """, unsafe_allow_html=True)
    
    # Tab 3: Statistics
    with tab3:
        st.header("URL Statistics")
        
        # Get stats
        url_stats, overall_stats = get_url_stats()
        
        # Display overall statistics
        if overall_stats:
            total_urls, total_clicks, max_clicks, avg_clicks = overall_stats
            
            st.markdown("<h3 class='stats-header'>Overall Statistics</h3>", unsafe_allow_html=True)
            
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.markdown(f"""
                <div class="stat-box">
                    <h2>{total_urls}</h2>
                    <p>Total URLs</p>
                </div>
                """, unsafe_allow_html=True)
            
            with col2:
                st.markdown(f"""
                <div class="stat-box">
                    <h2>{total_clicks or 0}</h2>
                    <p>Total Clicks</p>
                </div>
                """, unsafe_allow_html=True)
            
            with col3:
                st.markdown(f"""
                <div class="stat-box">
                    <h2>{max_clicks or 0}</h2>
                    <p>Most Clicks</p>
                </div>
                """, unsafe_allow_html=True)
            
            with col4:
                st.markdown(f"""
                <div class="stat-box">
                    <h2>{round(avg_clicks or 0, 1)}</h2>
                    <p>Avg Clicks</p>
                </div>
                """, unsafe_allow_html=True)
        
        # Display individual URL statistics
        st.markdown("<h3 class='stats-header'>Top URLs by Usage</h3>", unsafe_allow_html=True)
        
        if url_stats:
            for (code, url, count, created, last_accessed, expires, is_custom, notes) in url_stats:
                # Determine if URL is expired
                is_expired = expires and expires < datetime.datetime.now()
                card_class = "url-card"
                if is_expired:
                    card_class += " expired"
                elif is_custom:
                    card_class += " custom-url"
                
                # Create a nice looking card for each URL with copy buttons
                st.markdown(f"""
                <div class="{card_class}">
                    <h4>/{code} → {url[:50]}{'...' if len(url) > 50 else ''}</h4>
                    <div class="copy-input-container">
                        <input type="text" id="stat-url-{code}" class="copy-input" value="{base_url}?code={code}" readonly>
                        <button onclick="copyToClipboard('stat-url-{code}')" class="copy-btn copy-container-btn">Copy</button>
                        <span id="stat-url-{code}-msg" class="copied-message">Copied!</span>
                    </div>
                    <p>
                        <span style="margin-right:15px"><strong>Clicks:</strong> {count}</span>
                        <span style="margin-right:15px"><strong>Created:</strong> {created.strftime('%Y-%m-%d')}</span>
                        <span><strong>Status:</strong> {'Expired' if is_expired else 'Active'}</span>
                    </p>
                    {f'<p><strong>Notes:</strong> {notes}</p>' if notes else ''}
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("No URLs have been shortened yet")
    
    # Tab 4: Manage URLs
    with tab4:
        st.header("Manage URLs")
        
        # Simple admin password protection
        admin_password = st.text_input("Enter admin password to manage URLs:", type="password")
        
        if admin_password == st.secrets.get("admin_password", "admin"):  # Use a real password in secrets
            # Get stats for management
            url_stats, _ = get_url_stats()
            
            if url_stats:
                st.success("Authenticated! You can now manage URLs.")
                
                # Display management table
                st.markdown("Select URLs to delete:")
                
                for (code, url, count, created, last_accessed, expires, is_custom, notes) in url_stats:
                    col1, col2, col3 = st.columns([1, 3, 1])
                    
                    with col1:
                        st.code(code)
                    
                    with col2:
                        st.write(f"{url[:50]}{'...' if len(url) > 50 else ''}")
                        st.caption(f"Created: {created.strftime('%Y-%m-%d')} | Clicks: {count}")
                    
                    with col3:
                        if st.button(f"Delete {code}", key=f"del_{code}"):
                            if delete_url(code):
                                st.success(f"Deleted URL with code {code}")
                                st.rerun()
                            else:
                                st.error(f"Failed to delete URL with code {code}")
            else:
                st.info("No URLs to manage")
        elif admin_password:
            st.error("Incorrect password")

if __name__ == "__main__":
    main()