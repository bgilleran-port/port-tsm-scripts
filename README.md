# Port User Management Script

This script identifies and removes inactive Port users who have had no activity in the last 30 days.

## Features

- Identifies all Port users with inactive status
- Checks for any activity or updates in the last 30 days
- Backs up user entities to JSON files before deletion
- Creates a timestamped ZIP archive of all backups (format: `MM-DD-YYYY_deleted_users.zip`)
- Outputs a list of removed users to the terminal

## Prerequisites

- Python 3.7 or higher
- Port API credentials (Client ID and Client Secret)

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure environment variables:
   - Copy the example environment file:
     ```bash
     cp .env_example .env
     ```
   - Edit `.env` and add your Port API credentials:
     ```
     PORT_CLIENT_ID=your_client_id_here
     PORT_CLIENT_SECRET=your_client_secret_here
     PORT_API_URL=https://api.getport.io  # Optional, defaults to this value
     ```

   Alternatively, you can set environment variables directly:
   ```bash
   export PORT_CLIENT_ID="your_client_id"
   export PORT_CLIENT_SECRET="your_client_secret"
   ```

## Usage

Run the script:
```bash
python delete_inactive_users.py
```

## What the Script Does

1. **Authentication**: Authenticates with Port API using client credentials
2. **Fetch Users**: Retrieves all users from the `_user` blueprint
3. **Filter Inactive**: Identifies users with inactive status (checks for: inactive, Inactive, INACTIVE, Disabled, disabled, DISABLED)
4. **Check Activity**: For each inactive user, checks if they have had any updates in the last 30 days using the `updatedAt` field
5. **Backup**: Creates a JSON backup file for each user to be deleted
6. **Delete**: Removes the user from Port
7. **Archive**: Creates a ZIP file with timestamp containing all backup files
8. **Report**: Outputs a summary of removed users to the terminal

## Output

The script will:
- Create individual JSON backup files for each deleted user
- Create a ZIP archive named `MM-DD-YYYY_deleted_users.zip` (e.g., `12-04-2025_deleted_users.zip`)
- Print a summary to the terminal listing all removed users by name

## Error Handling

The script includes error handling for:
- Authentication failures
- API request failures
- Date parsing errors
- File I/O errors

If a user deletion fails, the backup file for that user will be removed to keep the archive clean.

## Troubleshooting

### 401 Unauthorized Error

If you encounter a 401 error, check the following:

1. **Verify Credentials**: Ensure your `PORT_CLIENT_ID` and `PORT_CLIENT_SECRET` are correct:
   - Check your `.env` file has the correct values (no extra spaces or quotes)
   - Verify credentials in your Port organization settings
   - Regenerate credentials if needed

2. **Check Environment Variables**: The script will show a partial client ID on startup. Verify it matches your credentials.

3. **API Access**: Ensure your Port organization has API access enabled and your credentials have the necessary permissions.

4. **API URL**: Verify the `PORT_API_URL` is correct (default: `https://api.getport.io`)

5. **Check Response**: The script will display the API response in error messages to help diagnose the issue.

## Notes

- The script checks the `updatedAt` field to determine activity. If a user has no `updatedAt` field, it falls back to `createdAt`.
- Users are considered inactive if their status matches any of: inactive, Inactive, INACTIVE, Disabled, disabled, DISABLED
- The activity threshold is set to 30 days by default (configurable via `DAYS_THRESHOLD` constant)

## Important: Entity Ownership

**When a user is deleted from Port, entities they created remain in the system.** They are not automatically deleted. This means:

- Entities created by deleted users will remain as "orphaned" entities
- These entities may still have relationships to other entities
- If you need to clean up entities created by deleted users, you'll need to:
  1. Query entities to find those created by the deleted user (check `createdBy` or ownership fields)
  2. Delete those entities separately using the Port API
  3. Handle any dependent entities appropriately

The current script only deletes the user entity itself, not entities created by that user.

