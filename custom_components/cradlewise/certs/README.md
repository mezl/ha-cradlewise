# Cradlewise mTLS Certificates

The crib's local MQTT broker requires mutual TLS authentication.
Place the following three files in this directory:

| File | Description |
|------|-------------|
| `amazon_root_ca1.pem` | Amazon Root CA 1 (broker CA) |
| `client.pem` | Client certificate (from the Cradlewise APK) |
| `client.key` | Client private key (from the Cradlewise APK) |

## Extracting the Certs

The certificates are bundled in the Cradlewise Android APK under
`assets/certs/`. To extract them:

```bash
# Download the APK from your device or apkmirror
apktool d cradlewise.apk -o cradlewise_unpacked
cp cradlewise_unpacked/assets/certs/amazon_root_ca1.pem ./
cp cradlewise_unpacked/assets/certs/client.pem ./
cp cradlewise_unpacked/assets/certs/client.key ./
```

These files are device-account-specific and must not be shared publicly.
