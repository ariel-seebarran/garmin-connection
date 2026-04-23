#!/bin/bash
# Coach Claude - Oracle Cloud Free Tier setup script
# Target: Ubuntu 22.04 LTS
# Run as a non-root user with sudo access (the default 'ubuntu' user is fine)
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${GREEN}[setup]${NC} $1"; }
warn()    { echo -e "${YELLOW}[warn]${NC}  $1"; }
confirm() { read -r -p "$(echo -e "${YELLOW}$1${NC} ")" REPLY; }

# ---------------------------------------------------------------------------
# Collect config up front
# ---------------------------------------------------------------------------
echo ""
echo "=== Coach Claude Setup ==="
echo ""
echo "You'll need:"
echo "  1. A domain name pointed at this server's public IP (e.g. coach.yourdomain.com)"
echo "  2. Your Google API key (for the AI coach)"
echo "  3. A password your friends will use to log in"
echo ""

confirm "Domain name (e.g. coach.yourdomain.com):"
DOMAIN="$REPLY"

confirm "Google API key:"
GOOGLE_API_KEY="$REPLY"

confirm "Password for friends to log in:"
HTPASSWD_PASS="$REPLY"

confirm "Your GitHub repo URL (e.g. https://github.com/YOU/garmin-connection):"
GITHUB_REPO="$REPLY"

APP_DIR="/opt/coach-claude"
APP_USER="coachclaude"

echo ""
info "Starting setup for $DOMAIN..."
echo ""

# ---------------------------------------------------------------------------
# 1. System packages
# ---------------------------------------------------------------------------
info "Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3.11 python3.11-venv python3-pip \
    nginx certbot python3-certbot-nginx \
    apache2-utils git iptables-persistent

# ---------------------------------------------------------------------------
# 2. App user & directory
# ---------------------------------------------------------------------------
info "Creating app user '$APP_USER'..."
sudo useradd -r -s /bin/bash -m -d "$APP_DIR" "$APP_USER" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 3. Clone repo
# ---------------------------------------------------------------------------
info "Cloning repo..."
if [ -d "$APP_DIR/app/.git" ]; then
    warn "Repo already exists — pulling latest."
    sudo -u "$APP_USER" git -C "$APP_DIR/app" pull
else
    sudo -u "$APP_USER" git clone "$GITHUB_REPO" "$APP_DIR/app"
fi

# ---------------------------------------------------------------------------
# 4. Python virtualenv
# ---------------------------------------------------------------------------
info "Creating Python virtualenv and installing dependencies..."
sudo -u "$APP_USER" python3.11 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --quiet \
    -r "$APP_DIR/app/backend/requirements.txt"

# ---------------------------------------------------------------------------
# 5. .env file
# ---------------------------------------------------------------------------
info "Writing .env..."
STRAVA_REDIRECT_URI="https://$DOMAIN/api/strava/callback"

sudo -u "$APP_USER" tee "$APP_DIR/app/.env" > /dev/null <<EOF
# Garmin credentials (optional — friends enter these in the UI instead)
GARMIN_EMAIL=
GARMIN_PASSWORD=

# Google API key (required)
GOOGLE_API_KEY=$GOOGLE_API_KEY

# Strava OAuth (optional — fill in if using Strava sync)
STRAVA_CLIENT_ID=
STRAVA_CLIENT_SECRET=
STRAVA_REDIRECT_URI=$STRAVA_REDIRECT_URI
EOF

chmod 600 "$APP_DIR/app/.env"
info ".env written. Edit $APP_DIR/app/.env later to add Strava credentials."

# ---------------------------------------------------------------------------
# 6. Systemd service
# ---------------------------------------------------------------------------
info "Setting up systemd service..."
sudo cp "$APP_DIR/app/deploy/coach-claude.service" /etc/systemd/system/
sudo sed -i "s|__APP_DIR__|$APP_DIR|g" /etc/systemd/system/coach-claude.service
sudo sed -i "s|__APP_USER__|$APP_USER|g" /etc/systemd/system/coach-claude.service
sudo systemctl daemon-reload
sudo systemctl enable coach-claude
sudo systemctl start coach-claude
info "Service started."

# ---------------------------------------------------------------------------
# 7. Basic auth password file
# ---------------------------------------------------------------------------
info "Creating basic auth password file..."
sudo htpasswd -cb /etc/nginx/.htpasswd coach "$HTPASSWD_PASS"
sudo chmod 640 /etc/nginx/.htpasswd
sudo chown root:www-data /etc/nginx/.htpasswd
info "Password set. Username is 'coach', password is what you just entered."

# ---------------------------------------------------------------------------
# 8. Nginx config
# ---------------------------------------------------------------------------
info "Configuring nginx..."
sudo cp "$APP_DIR/app/deploy/nginx.conf" /etc/nginx/sites-available/coach-claude
sudo sed -i "s|__DOMAIN__|$DOMAIN|g" /etc/nginx/sites-available/coach-claude
sudo ln -sf /etc/nginx/sites-available/coach-claude /etc/nginx/sites-enabled/coach-claude
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

# ---------------------------------------------------------------------------
# 9. Oracle Cloud firewall (iptables)
# ---------------------------------------------------------------------------
info "Opening ports 80 and 443 in OS firewall..."
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80  -j ACCEPT 2>/dev/null || true
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT 2>/dev/null || true
sudo netfilter-persistent save

# ---------------------------------------------------------------------------
# 10. SSL certificate
# ---------------------------------------------------------------------------
info "Obtaining SSL certificate from Let's Encrypt..."
echo ""
warn "Make sure your domain $DOMAIN is already pointing at this server's IP before continuing."
confirm "Press Enter when DNS is ready (or Ctrl+C to skip SSL for now):"

sudo certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
    --email "admin@$DOMAIN" --redirect

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo -e "${GREEN}=== Setup complete! ===${NC}"
echo ""
echo "  App URL:  https://$DOMAIN"
echo "  Username: coach"
echo "  Password: (what you entered)"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status coach-claude   # check if app is running"
echo "  sudo journalctl -u coach-claude -f   # live logs"
echo "  sudo -u $APP_USER git -C $APP_DIR/app pull && sudo systemctl restart coach-claude  # deploy updates"
echo ""
warn "IMPORTANT: You still need to open ports 80 and 443 in the OCI Security List"
warn "in the Oracle Cloud web console (Networking → Virtual Cloud Networks → Security Lists)."
echo ""
