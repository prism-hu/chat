#!/command/with-contenv sh
# s6 cont-init hook (bind-mounted to /etc/cont-init.d/ via docker-compose).
# NOTE: the `with-contenv` shebang is REQUIRED — plain cont-init scripts run with
# a minimal env and do NOT see the compose-provided container vars (X_*/IG_*).
#
# Bridges social-media credentials from the container environment (set in .env →
# docker-compose) into the per-skill credential FILES the skill scripts read.
#
# Why: the agent's code-execution sandbox does NOT inherit the gateway's env
# vars, so env-only secrets are invisible to skill scripts. Files under
# /opt/data (the volume) ARE readable by the sandbox and survive recreate. This
# hook gives us single-source-of-truth in .env while keeping the scripts working
# — and repopulates the files automatically on a fresh volume.
#
# Each var is written ONLY when non-empty (so it never clobbers a file you set by
# hand with a blank env). Files are chmod 600 + chowned to the hermes uid.
# Never fails boot (exits 0).

UID_="${HERMES_UID:-10000}"
GID_="${HERMES_GID:-10000}"

# write_secret VALUE PATH
write_secret() {
  val="$1"; path="$2"
  [ -n "$val" ] || return 0
  dir=$(dirname "$path")
  [ -d "$dir" ] || return 0   # skill not installed yet — skip
  printf %s "$val" > "$path" 2>/dev/null || return 0
  chmod 600 "$path" 2>/dev/null || true
  chown "$UID_:$GID_" "$path" 2>/dev/null || true
  echo "[social-creds] wrote $path"
}

X="/opt/data/skills/social-media/x-post"
IG="/opt/data/skills/social-media/ig-post"

write_secret "$X_USER"  "$X/.x_user"
write_secret "$X_EMAIL" "$X/.x_email"
write_secret "$X_PASS"  "$X/.x_pass"
write_secret "$X_TOTP"  "$X/.x_totp"

# Preferred X auth: browser cookies (auth_token + ct0). When both are set, build
# the twikit cookies file so the skill skips the (server-blocked) user/pass login.
if [ -n "$X_AUTH_TOKEN" ] && [ -n "$X_CT0" ] && [ -d "$X" ]; then
  printf '{"auth_token":"%s","ct0":"%s"}' "$X_AUTH_TOKEN" "$X_CT0" > "$X/.x_cookies.json" 2>/dev/null \
    && chmod 600 "$X/.x_cookies.json" 2>/dev/null \
    && chown "$UID_:$GID_" "$X/.x_cookies.json" 2>/dev/null
  echo "[social-creds] wrote $X/.x_cookies.json"
fi

write_secret "$IG_USER" "$IG/.ig_user"
write_secret "$IG_PASS" "$IG/.ig_pass"

exit 0
