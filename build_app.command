#!/bin/zsh
# Build, sign and (optionally) notarise Aegis.
#
#   Normal build:   ./build_app.command
#   Beta build:     BETA_EXPIRES=2026-12-31 BETA_TESTERS=10 ./build_app.command
#                   optional: BETA_CONTACT=you@company.com   BETA_NAMES="Ana,Bruno,Carla"   BETA_NO_KEY=1
#   Signing:        SIGN_ID="Developer ID Application: Your Name (TEAMID)"   NOTARY_PROFILE=<notarytool profile>
#
# A beta build gets an expiry date and one personal key per tester. The keys are saved to
# ~/Desktop/AegisBetaKeys/keys_<date>.csv (outside this folder). Send each tester only their own key.
set -e
cd "$(dirname "$0")"
source "${AEGIS_VENV:-$HOME/venv}/bin/activate"

CONFIG=aegis/beta_config.py
rm -f "$CONFIG"                       # start clean: a normal build must never contain a beta gate
trap 'rm -f "$CONFIG"' EXIT           # and never leave one behind in the source folder

# Fast checks only (the full suite takes ~15 minutes: run it before a release with: python -m pytest -q tests)
python -m pytest -q tests/test_core.py tests/test_v12.py tests/test_v13.py tests/test_robustness.py tests/test_beta.py

if [[ -n "$BETA_EXPIRES" ]]; then
  args=(--expires "$BETA_EXPIRES" --testers "${BETA_TESTERS:-5}")
  [[ -n "$BETA_CONTACT" ]] && args+=(--contact "$BETA_CONTACT")
  [[ -n "$BETA_NAMES" ]] && args+=(--names "$BETA_NAMES")
  [[ -n "$BETA_NO_KEY" ]] && args+=(--no-key)
  python tools/make_beta_config.py "${args[@]}" --out "$CONFIG"
fi

rm -rf build dist
pyinstaller --noconfirm Aegis_Privacy_Airlock.spec
APP="dist/Aegis Privacy Airlock.app"

if [[ -n "$SIGN_ID" ]]; then
  codesign --force --deep --options runtime --timestamp --sign "$SIGN_ID" "$APP"
  codesign --verify --deep --strict "$APP"
  if [[ -n "$NOTARY_PROFILE" ]]; then
    ditto -c -k --keepParent "$APP" dist/Aegis.zip
    xcrun notarytool submit dist/Aegis.zip --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$APP"
  fi
else
  echo "SIGN_ID not set: the app is unsigned. Testers must right-click the app and choose Open the first time."
fi

if [[ -n "$BETA_EXPIRES" ]]; then
  ZIP="dist/Aegis-Beta-$BETA_EXPIRES.zip"
  ditto -c -k --keepParent "$APP" "$ZIP"
  echo "Beta package: $ZIP   (expires $BETA_EXPIRES)"
fi
echo "Built: $APP"
