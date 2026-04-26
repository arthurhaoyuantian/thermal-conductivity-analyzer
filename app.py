import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import secrets
import base64
import hashlib
import hmac
import json
from flask import Flask, render_template, request, jsonify, redirect, session, url_for
from dotenv import load_dotenv

from src.process import process

load_dotenv()

# "geohub" = GeoHub SSO redirect when unauthenticated. "none", "local", "dev", "skip" = skip SSO for local testing.
_m = (os.environ.get("AUTH_METHOD") or "geohub").strip().lower()
AUTH_METHOD = _m if _m else "geohub"

#initialize flask application
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)

app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def _b64url_decode(input_str: str) -> bytes:
    s = input_str.replace("-", "+").replace("_", "/")
    padding = "=" * ((4 - (len(s) % 4)) % 4)
    return base64.b64decode(s + padding)

def _verify_geohub_sso_token(token: str) -> dict:
    """
    Token format: <base64url(payload-json)>.<base64url(hmac_sha256(payloadB64, secret))>
    Shared secret: GEOHUB_SSO_SHARED_SECRET
    """
    secret = os.getenv("GEOHUB_SSO_SHARED_SECRET", "")
    if not secret:
        raise ValueError("GEOHUB_SSO_SHARED_SECRET is not configured")

    try:
        payload_b64, sig_b64 = token.split(".", 1)
    except ValueError:
        raise ValueError("Invalid token format")

    expected_sig = hmac.new(
        secret.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    actual_sig = _b64url_decode(sig_b64)
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise ValueError("Invalid token signature")

    payload_raw = _b64url_decode(payload_b64).decode("utf-8")
    payload = json.loads(payload_raw)

    now = int(datetime.utcnow().timestamp())
    exp = int(payload.get("exp", 0) or 0)
    if exp and now > exp:
        raise ValueError("Token expired")

    aud_expected = os.getenv("TC_ANALYZER_SSO_AUDIENCE", "tc-analyzer")
    if payload.get("aud") and payload.get("aud") != aud_expected:
        raise ValueError("Invalid token audience")

    iss_expected = os.getenv("TC_ANALYZER_SSO_ISSUER", "geohub")
    if payload.get("iss") and payload.get("iss") != iss_expected:
        raise ValueError("Invalid token issuer")

    if not payload.get("sub"):
        raise ValueError("Token missing subject")

    return payload

def _geohub_sso_start_url(next_path: str) -> str | None:
    """
    Where tc-test-analyzer should send unauthenticated users to initiate SSO.
    Prefer explicit env; fall back to a best-effort guess from GEOHUB_URL.
    """
    explicit = os.getenv("GEOHUB_TC_ANALYZER_SSO_START_URL")
    if explicit:
        return f"{explicit}?next={next_path}"

    geohub_url = os.getenv("GEOHUB_URL")
    if geohub_url:
        return f"{geohub_url.rstrip('/')}/api/sso/tc-analyzer?next={next_path}"

    return None

@app.before_request
def require_auth():
    path = request.path or "/"
    if path.startswith("/static/"):
        return None
    if path in {"/auth/sso/callback", "/healthz", "/auth/logout"}:
        return None

    if AUTH_METHOD in ("none", "local", "dev", "skip"):
        if not session.get("user"):
            session["user"] = {
                "sub": os.getenv("AUTH_DEV_SUB", "local-dev"),
                "email": os.getenv("AUTH_DEV_EMAIL", "dev@local"),
            }
        return None

    if session.get("user"):
        return None

    # If we can initiate SSO, redirect; otherwise block.
    start = _geohub_sso_start_url(path)
    if start:
        return redirect(start)
    return jsonify({"error": "Unauthorized"}), 401

@app.get("/healthz")
def healthz():
    return "ok", 200

# SSO callback endpoint (called by Geohub)
@app.get("/auth/sso/callback")
def sso_callback():
    token = request.args.get("token", "")
    next_path = request.args.get("next", "/")
    if not next_path.startswith("/"):
        next_path = "/"

    try:
        claims = _verify_geohub_sso_token(token)
    except Exception as e:
        return jsonify({"error": f"SSO failed: {str(e)}"}), 401

    session["user"] = {
        "sub": claims.get("sub"),
        "email": claims.get("email"),
    }

    base = os.getenv("NEXT_PUBLIC_TC_ANALYZER_URL", "").rstrip("/")
    if base:
        return redirect(f"{base}{next_path}")
    return redirect(next_path)

@app.get("/auth/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

#home page
@app.route('/')
def home():
    return render_template('index.html')

#handles form submissions (communication w/ backend)
@app.route('/analyze', methods=['POST'])
def analyze(): 
    try:
        #process() parameters
        data_method = request.form.get('data_method')
        bh_depth = float(request.form.get('bh_depth'))
        overburden_depth = float(request.form.get('overburden_depth'))
        loop_od = float(request.form.get('loop_od'))
        pipe_sdr = request.form.get('pipe_sdr', 'sdr11')
        
        #rock segments
        segment_count = int(request.form.get('segment_count', 0))
        sections = []
        
        for i in range(segment_count):
            name = request.form.get(f'segment_{i}_name')
            start_depth = request.form.get(f'segment_{i}_start')
            end_depth = request.form.get(f'segment_{i}_end')
            tc_btu = request.form.get(f'segment_{i}_tc')
            
            if name and start_depth and end_depth and tc_btu:
                sections.append({
                    'name': name,
                    'start_depth': float(start_depth),
                    'end_depth': float(end_depth),
                    'tc_btu': float(tc_btu)
                })
        
        #CSV handling
        if data_method == 'CSV':
            
            if 'csv_file' not in request.files:
                return jsonify({'error': 'no file uploaded'}), 400
        
            file = request.files['csv_file']
            
            if file.filename == '':
                return jsonify({'error': 'no file selected'}), 400
            
            if file:
                original_filename = file.filename
                suffix = Path(original_filename).suffix or ".csv"
                unique_filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}_{secrets.token_hex(4)}{suffix}"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(filepath)
                
                results = process(
                    data_method='CSV',
                    csv_file_path=filepath,
                    rock_formation_segments=sections,
                    BH_DEPTH=bh_depth,
                    LOOP_OD=loop_od,
                    OVERBURDEN_DEPTH=overburden_depth,
                    PIPE_SDR=pipe_sdr,
                    START_DATE=None,
                    END_DATE=None
                )

                try:
                    os.remove(filepath)
                except:
                    pass
            
                if isinstance(results, str):
                    return jsonify({'error': results}), 400
                        
                return jsonify({
                    'success': True,
                    'method': 'CSV',
                    'results': results
                })
            
        elif data_method == 'API':
            #get dates form user 
            start_date = request.form.get('start_date')
            end_date = request.form.get('end_date')
                
            if not start_date or not end_date:
                return jsonify({'error': 'Start date and end date are required'}), 400
            #convert datetime-local strings to timestamps (milliseconds)
            
            toronto = ZoneInfo('America/Toronto')
            
            start_dt = datetime.fromisoformat(start_date).replace(tzinfo=toronto)
            end_dt = datetime.fromisoformat(end_date).replace(tzinfo=toronto)
            
            start_timestamp = int(start_dt.timestamp() * 1000)
            end_timestamp = int(end_dt.timestamp() * 1000)
            
            # Call your process function with API
            results = process(
                data_method='API',
                csv_file_path=None,
                rock_formation_segments=sections,
                BH_DEPTH=bh_depth,
                LOOP_OD=loop_od,
                OVERBURDEN_DEPTH=overburden_depth,
                PIPE_SDR=pipe_sdr,
                START_DATE=start_timestamp,
                END_DATE=end_timestamp
            )
            
            # Check if results is an error message
            if isinstance(results, str):
                return jsonify({'error': results}), 400
            
            return jsonify({
                'success': True,
                'method': 'API',
                'results': results
            })
        
        return jsonify({'error': 'Invalid data method'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500
        

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)