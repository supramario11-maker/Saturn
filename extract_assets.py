import os
import zipfile
import subprocess

def sign_ipa(input_ipa_path, output_ipa_path, p12_path, password, provisioning_profile):
    # 1. Unzip the target IPA file
    extract_dir = "temp_app"
    with zipfile.ZipFile(input_ipa_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)
    
    # 2. Locate the .app bundle inside Payload
    payload_dir = os.path.join(extract_dir, "Payload")
    app_name = os.listdir(payload_dir)[0]
    app_bundle_path = os.path.join(payload_dir, app_name)
    
    # 3. Swap the embedded provisioning profile with your Rakuten profile
    target_profile_path = os.path.join(app_bundle_path, "embedded.mobileprovision")
    if os.path.exists(target_profile_path):
        os.remove(target_profile_path)
    subprocess.run(["cp", provisioning_profile, target_profile_path])
    
    # 4. Use standard cryptographic tools (like codesign or zsign) to sign using the P12
    # This unlocks the private key and signs the Mach-O binary structures natively
    signing_command = [
        "zsign", 
        "-k", p12_path, 
        "-p", password, 
        "-m", provisioning_profile, 
        "-o", output_ipa_path, 
        app_bundle_path
    ]
    subprocess.run(signing_command)
    print(f"Successfully signed application package: {output_ipa_path}")
