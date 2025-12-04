#!/usr/bin/env python3
"""
Script to identify and delete inactive Port users with no activity in the last 30 days.
Backs up user data before deletion and creates a timestamped zip archive.

IMPORTANT NOTE: When a user is deleted, entities they created remain in Port.
They are not automatically deleted. If you need to clean up entities created by
deleted users, you'll need to handle that separately.
"""

import requests
import json
import os
import zipfile
from datetime import datetime, timedelta
from typing import List, Dict
import sys
from dotenv import load_dotenv
from pathlib import Path
from urllib.parse import quote

# Determine if .env file exists and load it
env_path = Path('.env')
env_file_exists = env_path.exists()

# Check if variables are already set in environment (from .zshrc, etc.)
# BEFORE loading .env file
client_id_from_env = os.getenv('PORT_CLIENT_ID', '')
client_secret_from_env = os.getenv('PORT_CLIENT_SECRET', '')

# Load environment variables from .env file
# Note: By default, load_dotenv() does NOT override existing environment variables
# If you want .env to override .zshrc, use: load_dotenv(override=True)
dotenv_loaded = load_dotenv()

# Configuration - Update these with your Port credentials
PORT_API_BASE_URL = os.getenv('PORT_API_URL', 'https://api.getport.io')
PORT_CLIENT_ID = os.getenv('PORT_CLIENT_ID', '')
PORT_CLIENT_SECRET = os.getenv('PORT_CLIENT_SECRET', '')

# Headers for API requests
HEADERS = {
    'Content-Type': 'application/json'
}

# Constants
BLUEPRINT_IDENTIFIER = '_user'
INACTIVE_STATUS_VALUES = ['inactive', 'Inactive', 'INACTIVE', 'Disabled', 'disabled', 'DISABLED']
DAYS_THRESHOLD = 30
BACKUP_DIR = 'user_backups'


def get_port_access_token() -> str:
    """
    Get Port API access token using client credentials.
    """
    if not PORT_CLIENT_ID or not PORT_CLIENT_SECRET:
        raise ValueError(
            "PORT_CLIENT_ID and PORT_CLIENT_SECRET must be set as environment variables"
        )
    
    # Port API uses /v1/auth/access_token endpoint
    auth_url = f"{PORT_API_BASE_URL}/v1/auth/access_token"
    payload = {
        "clientId": PORT_CLIENT_ID,
        "clientSecret": PORT_CLIENT_SECRET
    }
    
    try:
        response = requests.post(auth_url, json=payload, headers=HEADERS)
        
        # Better error handling for 401
        if response.status_code == 401:
            error_msg = "Authentication failed (401). "
            error_msg += "Please verify your PORT_CLIENT_ID and PORT_CLIENT_SECRET are correct. "
            error_msg += f"Response: {response.text}"
            raise ValueError(error_msg)
        
        response.raise_for_status()
        data = response.json()
        
        # Handle different possible response keys
        access_token = data.get('accessToken') or data.get('access_token')
        if not access_token:
            raise ValueError(f"Unexpected response format from auth endpoint: {data}")
        
        return access_token
    except requests.exceptions.RequestException as e:
        if hasattr(e, 'response') and e.response is not None:
            error_msg = f"Failed to authenticate with Port API: {e}\n"
            error_msg += f"Status Code: {e.response.status_code}\n"
            error_msg += f"Response: {e.response.text}"
            raise ValueError(error_msg) from e
        raise


def get_all_users(access_token: str) -> List[Dict]:
    """
    Fetch all users from Port using GET entities API.
    Endpoint: GET /v1/blueprints/{blueprint_identifier}/entities
    """
    # Correct endpoint format: /v1/blueprints/{blueprint_identifier}/entities
    url = f"{PORT_API_BASE_URL}/v1/blueprints/{BLUEPRINT_IDENTIFIER}/entities"
    headers = {
        **HEADERS,
        'Authorization': f'Bearer {access_token}'
    }
    
    # Try without query parameters first - Port API may return all entities
    # or may not support pagination via query params for this endpoint
    try:
        response = requests.get(
            url,
            headers=headers
        )
        
        # Better error handling
        if response.status_code == 422:
            error_data = {}
            try:
                error_data = response.json()
            except (ValueError, json.JSONDecodeError):
                pass
            
            error_msg = "422 Unprocessable Entity - Invalid request format\n"
            error_msg += f"Request URL: {response.url}\n"
            error_msg += f"Response status: {response.status_code}\n"
            error_msg += f"Response body: {response.text}"
            if error_data:
                error_msg += f"\nParsed error: {json.dumps(error_data, indent=2)}"
            raise ValueError(error_msg)
        
        response.raise_for_status()
        
    except requests.exceptions.HTTPError as e:
        if hasattr(e, 'response') and e.response is not None:
            error_msg = f"Error fetching users: {e}\n"
            error_msg += f"Status Code: {e.response.status_code}\n"
            error_msg += f"Response: {e.response.text}\n"
            if hasattr(e.response, 'url'):
                error_msg += f"Request URL: {e.response.url}"
            raise ValueError(error_msg) from e
        raise
    
    data = response.json()
    # Handle different possible response structures
    users = data.get('entities', [])
    if not users and isinstance(data, list):
        users = data
    
    return users


def is_inactive(user: Dict) -> bool:
    """
    Check if user has inactive status.
    """
    # Check in properties first, then at root level
    status = user.get('properties', {}).get('status', '') or user.get('status', '')
    # Normalize status for comparison
    status_lower = str(status).lower() if status else ''
    return status_lower in [s.lower() for s in INACTIVE_STATUS_VALUES]


def has_recent_activity(user: Dict, days_threshold: int = DAYS_THRESHOLD) -> bool:
    """
    Check if user has had any activity (updates) in the last N days.
    Returns True if updated within threshold, False otherwise.
    """
    updated_at = user.get('updatedAt')
    if not updated_at:
        # If no updatedAt, check createdAt
        created_at = user.get('createdAt')
        if not created_at:
            return False
        updated_at = created_at
    
    try:
        # Parse ISO 8601 datetime
        if 'T' in updated_at:
            # Remove timezone info if present for parsing
            updated_at_clean = updated_at.split('+')[0].split('Z')[0]
            if '.' in updated_at_clean:
                updated_at_clean = updated_at_clean.split('.')[0]
            last_update = datetime.strptime(updated_at_clean, '%Y-%m-%dT%H:%M:%S')
        else:
            last_update = datetime.strptime(updated_at, '%Y-%m-%d')
        
        threshold_date = datetime.now() - timedelta(days=days_threshold)
        return last_update >= threshold_date
    except Exception as e:
        print(f"Warning: Could not parse date '{updated_at}' for user {user.get('identifier')}: {e}")
        # If we can't parse the date, assume no recent activity to be safe
        return False


def backup_user(user: Dict, backup_dir: str) -> str:
    """
    Backup user entity to a JSON file.
    Returns the path to the backup file.
    """
    user_id = user.get('identifier', 'unknown')
    backup_file = os.path.join(backup_dir, f'{user_id}.json')
    
    with open(backup_file, 'w', encoding='utf-8') as f:
        json.dump(user, f, indent=2, ensure_ascii=False)
    
    return backup_file


def delete_user(access_token: str, user_identifier: str) -> bool:
    """
    Delete a user from Port.
    Endpoint: DELETE /v1/blueprints/{blueprint_identifier}/entities/{entity_identifier}
    Returns True if successful, False otherwise.
    """
    # URL encode the identifier to handle special characters like +, @, etc.
    encoded_identifier = quote(user_identifier, safe='')
    url = f"{PORT_API_BASE_URL}/v1/blueprints/{BLUEPRINT_IDENTIFIER}/entities/{encoded_identifier}"
    headers = {
        **HEADERS,
        'Authorization': f'Bearer {access_token}'
    }
    
    try:
        response = requests.delete(url, headers=headers)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error deleting user {user_identifier}: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response: {e.response.text}")
            print(f"Request URL: {url}")
        return False


def create_zip_archive(backup_dir: str, zip_filename: str) -> None:
    """
    Create a zip archive of all backup files.
    """
    with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(backup_dir):
            for file in files:
                if file.endswith('.json'):
                    file_path = os.path.join(root, file)
                    zipf.write(file_path, arcname=file)
    
    print(f"\nBackup archive created: {zip_filename}")


def cleanup_backup_dir(backup_dir: str) -> None:
    """
    Remove backup directory and its contents.
    """
    if os.path.exists(backup_dir):
        for file in os.listdir(backup_dir):
            os.remove(os.path.join(backup_dir, file))
        os.rmdir(backup_dir)


def main():
    """
    Main execution function.
    """
    print("Starting inactive user cleanup process...")
    print(f"Looking for users with inactive status and no activity in the last {DAYS_THRESHOLD} days\n")
    
    # Debug: Show credential source information
    print("="*60)
    print("CREDENTIAL SOURCE DEBUG")
    print("="*60)
    
    env_file_path = Path('.env').absolute()
    env_exists = env_file_path.exists()
    
    print(f".env file exists: {env_exists}")
    if env_exists:
        print(f".env file path: {env_file_path}")
        print(f".env file readable: {os.access(env_file_path, os.R_OK)}")
    else:
        print(f".env file path (not found): {env_file_path}")
    
    # Check if variables were set before load_dotenv()
    had_env_vars_before = bool(client_id_from_env and client_secret_from_env)
    print(f"Environment variables set before load_dotenv(): {had_env_vars_before}")
    
    # Check current state
    has_credentials = bool(PORT_CLIENT_ID and PORT_CLIENT_SECRET)
    print(f"Credentials loaded: {has_credentials}")
    
    if has_credentials:
        # Try to determine source by checking if .env was loaded
        if env_exists and not had_env_vars_before:
            print("Source: .env file (credentials were NOT in environment before)")
        elif had_env_vars_before:
            print("Source: Environment variables (from .zshrc or shell)")
            if env_exists:
                print("  ⚠️  WARNING: .env file exists but load_dotenv() does NOT override")
                print("     existing environment variables by default.")
                print("     Your .zshrc values are being used, not .env file values!")
                print("     To use .env file, either:")
                print("     1. Unset variables: unset PORT_CLIENT_ID PORT_CLIENT_SECRET")
                print("     2. Or modify script to use: load_dotenv(override=True)")
        else:
            print("Source: Unknown (check manually)")
    
    print("="*60)
    print()
    
    # Debug: Check if credentials are loaded (without showing values)
    if not PORT_CLIENT_ID or not PORT_CLIENT_SECRET:
        print("ERROR: PORT_CLIENT_ID or PORT_CLIENT_SECRET not set!")
        print("Please check your .env file or environment variables.")
        sys.exit(1)
    
    print(f"Port API URL: {PORT_API_BASE_URL}")
    print(f"Client ID: {PORT_CLIENT_ID[:8]}..." if len(PORT_CLIENT_ID) > 8 else "Client ID: [not set]")
    print()
    
    # Get access token
    try:
        print("Authenticating with Port API...")
        access_token = get_port_access_token()
        print("Authentication successful\n")
    except Exception as e:
        print(f"Error authenticating: {e}")
        print("\nTroubleshooting tips:")
        print("1. Verify your PORT_CLIENT_ID and PORT_CLIENT_SECRET in .env file")
        print("2. Ensure credentials are correct and have not expired")
        print("3. Check that your Port organization has API access enabled")
        sys.exit(1)
    
    # Fetch all users
    try:
        print("Fetching all users from Port...")
        all_users = get_all_users(access_token)
        print(f"Found {len(all_users)} total users\n")
    except Exception as e:
        print(f"Error fetching users: {e}")
        sys.exit(1)
    
    # Filter inactive users
    inactive_users = [user for user in all_users if is_inactive(user)]
    print(f"Found {len(inactive_users)} users with inactive status\n")
    
    # Identify users with no recent activity
    users_to_delete = []
    for user in inactive_users:
        if not has_recent_activity(user, DAYS_THRESHOLD):
            users_to_delete.append(user)
    
    print(f"Found {len(users_to_delete)} inactive users with no activity in the last {DAYS_THRESHOLD} days\n")
    
    if not users_to_delete:
        print("No users to delete. Exiting.")
        return
    
    # Create backup directory
    os.makedirs(BACKUP_DIR, exist_ok=True)
    
    # Process each user: backup and delete
    removed_users = []
    failed_deletions = []
    
    for user in users_to_delete:
        user_identifier = user.get('identifier', 'unknown')
        user_title = user.get('title', user_identifier)
        
        try:
            # Backup user
            backup_file = backup_user(user, BACKUP_DIR)
            print(f"Backed up user: {user_title} ({user_identifier})")
            
            # Delete user
            if delete_user(access_token, user_identifier):
                removed_users.append(user_title)
                print(f"Deleted user: {user_title} ({user_identifier})")
            else:
                failed_deletions.append(user_title)
                # Remove backup file if deletion failed
                if os.path.exists(backup_file):
                    os.remove(backup_file)
                print(f"Failed to delete user: {user_title} ({user_identifier})")
        except Exception as e:
            print(f"Error processing user {user_title} ({user_identifier}): {e}")
            failed_deletions.append(user_title)
    
    # Create zip archive
    if removed_users:
        timestamp = datetime.now().strftime('%m-%d-%Y')
        zip_filename = f"{timestamp}_deleted_users.zip"
        
        try:
            create_zip_archive(BACKUP_DIR, zip_filename)
        except Exception as e:
            print(f"Error creating zip archive: {e}")
    
    # Clean up backup directory
    cleanup_backup_dir(BACKUP_DIR)
    
    # Output results
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    
    if removed_users:
        print(f"\nSuccessfully removed {len(removed_users)} user(s):")
        for name in removed_users:
            print(f"  - {name}")
    else:
        print("\nNo users were removed.")
    
    if failed_deletions:
        print(f"\nFailed to delete {len(failed_deletions)} user(s):")
        for name in failed_deletions:
            print(f"  - {name}")
    
    print("\n" + "="*60)


if __name__ == '__main__':
    main()

