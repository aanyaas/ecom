import os
import subprocess
import datetime
import zipfile
import glob
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

BACKUP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backups')
MAX_BACKUP_DAYS = 7

def perform_database_backup():
    """
    Automated job to backup the MySQL database to a compressed zip file.
    It removes backups older than MAX_BACKUP_DAYS to save space.
    """
    print(f"[{datetime.datetime.now()}] Starting Automated Database Backup...")
    
    # Ensure backup directory exists
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)

    # Database credentials from environment
    db_host = os.getenv("DB_HOST", "localhost")
    db_user = os.getenv("DB_USER", "root")
    db_password = os.getenv("DB_PASSWORD", "")
    db_name = os.getenv("DB_NAME", "ecommerce_db")

    # Generate filenames
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    sql_filename = f"{db_name}_backup_{timestamp}.sql"
    sql_filepath = os.path.join(BACKUP_DIR, sql_filename)
    zip_filename = f"{db_name}_backup_{timestamp}.zip"
    zip_filepath = os.path.join(BACKUP_DIR, zip_filename)

    # Build mysqldump command
    dump_cmd = [
        "mysqldump",
        "-h", db_host,
        "-u", db_user
    ]
    if db_password:
        dump_cmd.append(f"-p{db_password}")
    dump_cmd.append(db_name)

    try:
        # Run mysqldump
        with open(sql_filepath, "w", encoding="utf-8") as outfile:
            process = subprocess.run(dump_cmd, stdout=outfile, stderr=subprocess.PIPE, text=True)
            
        if process.returncode != 0:
            print(f"Backup failed. mysqldump error: {process.stderr}")
            if os.path.exists(sql_filepath):
                os.remove(sql_filepath)
            return False

        # Compress to zip
        with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(sql_filepath, arcname=sql_filename)
            
        # Delete original uncompressed sql file
        os.remove(sql_filepath)
        print(f"[{datetime.datetime.now()}] Backup successfully created at: {zip_filepath}")

        # Cleanup old backups
        cleanup_old_backups()
        
        return True

    except FileNotFoundError:
        print("ERROR: 'mysqldump' not found. Ensure MySQL bin is added to your Windows PATH.")
        if os.path.exists(sql_filepath):
            os.remove(sql_filepath)
        return False
    except Exception as e:
        print(f"ERROR: Backup process failed: {e}")
        return False

def cleanup_old_backups():
    """Delete backups older than MAX_BACKUP_DAYS."""
    now = datetime.datetime.now()
    search_pattern = os.path.join(BACKUP_DIR, "*.zip")
    
    for file_path in glob.glob(search_pattern):
        # Get file creation time
        file_mtime = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
        age_days = (now - file_mtime).days
        
        if age_days > MAX_BACKUP_DAYS:
            try:
                os.remove(file_path)
                print(f"[{datetime.datetime.now()}] Deleted old backup: {os.path.basename(file_path)}")
            except Exception as e:
                print(f"Error deleting old backup {file_path}: {e}")

if __name__ == "__main__":
    # Test the backup script when run directly
    perform_database_backup()
