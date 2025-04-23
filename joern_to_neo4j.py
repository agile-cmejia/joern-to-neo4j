#!/usr/bin/env python3
import os
import subprocess
import argparse
import sys
import re
import csv
import logging
from pathlib import Path # Use pathlib for better path handling
from neo4j import GraphDatabase, basic_auth, exceptions as neo4j_exceptions
import shutil # Added for file copying
# --- Configuration ---
# Set up basic logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s")


# Default JVM memory allocation for Joern commands (adjust as needed)
DEFAULT_JVM_MEM = "-J-Xmx4G"
# Batch size for IN TRANSACTIONS clause
BATCH_SIZE = 1000

# --- Helper Functions ---

def run_command(command, cwd=None):
    """Executes a shell command and logs output."""
    logging.info(f"Running command: {' '.join(command)}")
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding='utf-8', # Explicitly set encoding
            errors='replace', # Handle potential encoding errors in output
            cwd=cwd
        )
        # Log stdout only if it contains content, to avoid clutter
        if result.stdout and result.stdout.strip():
            logging.info(f"Command stdout:\n{result.stdout}")
        # Log stderr as warning only if it contains content
        if result.stderr and result.stderr.strip():
            logging.warning(f"Command stderr:\n{result.stderr}")
        return True, result.stdout
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed: {' '.join(e.cmd)}")
        logging.error(f"Return code: {e.returncode}")
        # Decode stderr/stdout safely if they exist
        stderr_output = e.stderr.strip() if e.stderr else "N/A"
        stdout_output = e.stdout.strip() if e.stdout else "N/A"
        logging.error(f"Stderr: {stderr_output}")
        logging.error(f"Stdout: {stdout_output}")
        return False, stderr_output
    except FileNotFoundError:
        logging.error(f"Error: Command not found: {command}. Is it installed and in PATH?")
        return False, f"Command not found: {command}"
    except Exception as e:
        logging.error(f"An unexpected error occurred running command {' '.join(command)}: {e}")
        return False, str(e)

def run_joern_parse(input_path, cpg_output_path, jvm_mem=DEFAULT_JVM_MEM):
    """Runs Joern parse to generate CPG."""
    logging.info(f"Starting Joern parse for: {input_path}")

    command = ["joern-parse", jvm_mem, input_path, "--output", cpg_output_path]
    success, _ = run_command(command)
    if success:
        logging.info(f"Joern parsing successful. CPG saved to: {cpg_output_path}")
        return cpg_output_path
    else:
        logging.error("Joern parsing failed.")
        return None

def run_joern_export(cpg_input_path, csv_output_dir, export_format="neo4jcsv", jvm_mem=DEFAULT_JVM_MEM):
    """Runs Joern export to generate CSV files."""
    logging.info(f"Starting Joern export for: {cpg_input_path}")

    command = [
        "joern-export",
        jvm_mem,
        cpg_input_path,
        "--repr", "all",
        "--format", export_format,
        "--out", csv_output_dir
    ]
    success, _ = run_command(command)
    if success:
        logging.info(f"Joern export successful. Files exported to: {csv_output_dir}")
        return csv_output_dir
    else:
        logging.error("Joern export failed.")
        return None

def get_cypher_files(output_dir: Path) -> tuple[list[Path], list[Path]]:
    """
    Finds node and edge Cypher import files (*_cypher.csv) in the specified directory.

    Args:
        output_dir: The Path object representing the directory to search.

    Returns:
        A tuple containing two lists:
        - node_cypher_files: List of Paths to node cypher files (nodes_*_cypher.csv).
        - edge_cypher_files: List of Paths to edge cypher files (edges_*_cypher.csv).

    Raises:
        FileNotFoundError: If the output_dir does not exist or is not a directory.
    """
    if not output_dir.is_dir():
        logging.error(f"Output directory not found or is not a directory: {output_dir}")
        raise FileNotFoundError(f"Output directory not found: {output_dir}")

    logging.info(f"Searching for Neo4j Cypher import files (*_cypher.csv) in: {output_dir}")
    node_cypher_files = sorted(list(output_dir.glob("nodes_*_cypher.csv")))
    # Using edges_ prefix as per user's code
    edge_cypher_files = sorted(list(output_dir.glob("edges_*_cypher.csv")))

    logging.info(f"Found {len(node_cypher_files)} node cypher files.")
    logging.info(f"Found {len(edge_cypher_files)} edge cypher files.")

    if not node_cypher_files and not edge_cypher_files:
        logging.warning(f"No '*_cypher.csv' files found in {output_dir}. Ensure Joern export generated these files correctly.")

    return node_cypher_files, edge_cypher_files


def import_online_neo4j(
    driver, database_name: str, node_cypher_files: list[Path], edge_cypher_files: list[Path], output_dir: Path
):
    """
    Imports data into Neo4j by executing pre-written Cypher queries found in files,
    modifying them for absolute paths and batching.

    Args:
        driver: The Neo4j driver instance.
        database_name: The name of the target Neo4j database.
        node_cypher_files: List of Paths to node cypher files.
        edge_cypher_files: List of Paths to edge cypher files.
        output_dir: The Path object representing the base directory containing the cypher and data files.
    """
    logging.info(f"Starting Neo4j online import into database '{database_name}'.")
    logging.warning("-" * 80)


    # Regex to find LOAD CSV and extract components
    # Handles optional "WITH HEADERS" and captures the base file path and variable name
    # Made more robust to handle potential variations in spacing
    pattern_load_csv = re.compile(
        r"(LOAD\s+CSV(?:\s+WITH\s+HEADERS)?\s+FROM\s+)'file:/([^']+)'(\s+AS\s+(\w+))",
        re.IGNORECASE
    )

    # --- Create Constraints (Attempt) ---
    # Assuming a common pattern for Joern nodes. Adjust if needed.
    constraint_query = "CREATE CONSTRAINT JoernNodeIdConstraint IF NOT EXISTS FOR (n:JoernNode) REQUIRE n.id IS UNIQUE"
    constraint_applied = False
    try:
        with driver.session(database=database_name) as session:
            logging.info(f"Attempting to apply constraint to database '{database_name}': {constraint_query}")
            session.execute_write(lambda tx: tx.run(constraint_query).consume())
            logging.info("Constraint check/creation successful or constraint already existed.")
            constraint_applied = True
    except neo4j_exceptions.ClientError as e:
        logging.error(f"Failed to apply constraint to database '{database_name}': {e.code} - {e.message}")
        logging.error("Check if 'id' property exists on JoernNode or if constraint syntax is valid.")
    except Exception as e:
        logging.error(f"An unexpected error occurred during constraint creation: {type(e).__name__} - {e}")

    if not constraint_applied:
        logging.warning("Constraint application failed or was skipped. Performance might be impacted if queries use MERGE.")

    # --- Process Cypher Files ---
    all_cypher_files = node_cypher_files + edge_cypher_files
    import_errors = False
    processed_files = 0 # Initialize counter

    for cypher_file_path in all_cypher_files:
        logging.info(f"\n--- Processing Cypher File: {cypher_file_path.name} ---")
        try:
            # 1. Read Cypher Query
            cypher_content = cypher_file_path.read_text(encoding='utf-8')
            if not cypher_content.strip():
                logging.warning(f"Cypher file is empty, skipping: {cypher_file_path.name}")
                continue

            # 2. Find LOAD CSV and derive data file path
            match = pattern_load_csv.search(cypher_content[0:-1])
            if not match:
                logging.error(f"Could not find 'LOAD CSV FROM 'file:/...'' pattern in {cypher_file_path.name}. Cannot modify for import. Skipping file.")
                logging.error("Expected format: LOAD CSV FROM 'file:/<filename>_data.csv' AS <variable>")
                import_errors = True
                continue

            load_clause_prefix = match.group(1) # "LOAD CSV..."
            relative_data_filename = match.group(2) # "<filename>_data.csv"
            load_clause_suffix = match.group(3) # " AS <variable>"
            variable_name = match.group(4) # "<variable>" (e.g., 'line')

            # Construct absolute path for the corresponding _data.csv file
            # Ensure the data file is looked for in the same directory as the cypher file
            data_file_path = cypher_file_path.parent.resolve() / Path(relative_data_filename).name
            if not data_file_path.is_file():
                logging.error(f"Corresponding data file not found for {cypher_file_path.name}: {data_file_path}")
                logging.error("Ensure the '_data.csv' file exists in the same directory as the '_cypher.csv' file.")
                import_errors = True
                continue

            # Convert to relative file:/// URI (Neo4j resolves against its import dir)
            data_file_abs_uri = f"file:///{data_file_path.name}"
            logging.info(f"Data file URI for Neo4j (relative): {data_file_abs_uri}")

            # 3. Modify Cypher: Replace relative path with absolute URI
            modified_cypher = pattern_load_csv.sub(
                rf"{load_clause_prefix}'{data_file_abs_uri}'{load_clause_suffix}",
                cypher_content,
                count=1 # Replace only the first occurrence
            )

            # 4. Modify Cypher: Wrap core logic in CALL {} IN TRANSACTIONS
            # Need to re-search in the *modified* string to get correct indices
            match_modified = pattern_load_csv.search(modified_cypher)
            if not match_modified:
                logging.error(f"Internal error: Could not re-match LOAD CSV pattern in modified query for {cypher_file_path.name}. Skipping.")
                import_errors = True
                continue

            load_clause_end_index = match_modified.end(0)
            load_clause_full = modified_cypher[:load_clause_end_index]
            core_logic = modified_cypher[load_clause_end_index:].strip()

            if not core_logic:
                logging.warning(f"No core Cypher logic found after LOAD CSV clause in {cypher_file_path.name}. Skipping execution.")
                continue

            # Construct the final batched query
            batched_cypher = (
                f"{load_clause_full}\n"
                f"CALL {{\n"
                f"    WITH {variable_name}\n"
                # Indent core logic for readability (optional)
                f"    {core_logic[0:-1].replace(chr(10), chr(10) + '    ')}\n"
                f"}} IN TRANSACTIONS OF {BATCH_SIZE} ROWS\n"
                # Add a final return for better feedback, though consume() is used
                f"RETURN 'Batch processed from {cypher_file_path.name}'"
            )

            # log.debug(f"Modified Cypher for {cypher_file_path.name}:\n{batched_cypher}") # Uncomment for debugging

            # 5. Execute Modified Cypher using session.run() for implicit transaction
            with driver.session(database=database_name) as session:
                try:
                    logging.info(f"Executing modified Cypher from: {cypher_file_path.name}")
                    # Use run().consume() directly for implicit transaction suitable for CALL {} IN TRANSACTIONS
                    summary = session.run(batched_cypher).consume()
                    logging.info(f"Successfully executed batch from {cypher_file_path.name}. Counters: {summary.counters}")
                    processed_files += 1 # Increment counter ONLY on success
                except neo4j_exceptions.ClientError as e:
                    # Re-raise ClientError to be caught and handled by the outer loop's specific logging
                    raise e
                except Exception as e:
                    # Re-raise other unexpected errors to be caught and handled by the outer loop's generic logging
                    raise e

        except FileNotFoundError:
            logging.error(f"Cypher file not found during processing loop: {cypher_file_path}. This should not happen if discovery worked.")
            import_errors = True
        except neo4j_exceptions.ClientError as e:
            logging.error(f"Neo4j ClientError during import of {cypher_file_path.name}: {e.code} - {e.message}")
            if "constraint" in str(e.message).lower():
                logging.error("Hint: Check for data violating uniqueness constraints.")
            elif "apoc" in str(e.message).lower():
                logging.error("Hint: Ensure APOC plugin is installed and configured in Neo4j if the Cypher query uses APOC procedures.")
            elif "file access" in str(e.message).lower() or "couldn't load file" in str(e.message).lower() or "directory not configured" in str(e.message).lower():
                logging.error(f"Hint: Neo4j server likely cannot access the data file URI: {data_file_abs_uri}")
                logging.error(f"Hint: Verify Neo4j's 'dbms.directories.import' setting and file system permissions for the Neo4j process.")
            elif "transaction" in str(e.message).lower():
                logging.error("Hint: The error occurred during transaction processing, potentially related to batching or query complexity.")
            import_errors = True
        except Exception as e:
            logging.error(f"An unexpected error occurred processing {cypher_file_path.name}: {type(e).__name__} - {e}")
            import_errors = True

    logging.info(f"\nNeo4j import process finished for database '{database_name}'.")
    logging.info(f"Processed {processed_files} cypher files.")
    if import_errors:
        logging.warning("Import finished, but errors occurred during the process. Please review logs.")
        return False
    elif processed_files == 0 and (node_cypher_files or edge_cypher_files):
        logging.warning("Import finished, but no files were successfully processed (check logs for errors).")
        return False
    elif processed_files == 0 and not (node_cypher_files or edge_cypher_files):
        logging.info("Import finished. No cypher files were found to process.")
        return True # Technically successful if no files were expected
    else:
        logging.info("Import completed successfully (based on lack of logged errors).")
        return True
# --- Main Execution ---

def main():
    parser = argparse.ArgumentParser(
        description="Automate Joern CPG generation and Neo4j online import.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter # Show defaults in help
    )
    parser.add_argument("input_path", help="Path to the source code file or directory to analyze.")
    parser.add_argument("-o", "--output-dir", default="joern_neo4j_output",
                        help="Directory to store intermediate CPG and CSV files.")
    parser.add_argument("--jvm-mem", default=DEFAULT_JVM_MEM,
                        help="JVM memory allocation for Joern commands (e.g., -J-Xmx8G).")

    # Arguments for Online Mode (now the only mode)
    parser.add_argument("--neo4j-uri", default=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
                            help="Neo4j Bolt URI. Reads from NEO4J_URI env var if set.")
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USER", "neo4j"),
                            help="Neo4j username. Reads from NEO4J_USER env var if set.")
    parser.add_argument("--neo4j-password", required=False, # Made optional to allow env var usage
                            help="Neo4j password. If not provided, reads from NEO4J_PASSWORD env var.")
    parser.add_argument("--neo4j-database", default=os.getenv("NEO4J_DATABASE", "neo4j"),
                            help="Target Neo4j database name. Reads from NEO4J_DATABASE env var if set.")
    parser.add_argument("--neo4j-import-dir", default=os.getenv("NEO4J_IMPORT_DIR", "joern_neo4j_import"),
                            help="Directory to store intermediate CPG and CSV files. Within the Neo4j import directory")

    # Set password from environment variable if not provided via argument
    args = parser.parse_args()
    if not args.neo4j_password:
        args.neo4j_password = os.getenv("NEO4J_PASSWORD")
        if not args.neo4j_password:
            logging.error("Neo4j password is required. Set --neo4j-password or NEO4J_PASSWORD environment variable.")
            sys.exit(1)


    # --- Validate Arguments ---
    if not os.path.exists(args.input_path):
        logging.error(f"Input path not found: {args.input_path}")
        sys.exit(1)
    if not os.path.isdir(args.input_path) and not os.path.isfile(args.input_path):
        logging.error(f"Input path is not a valid file or directory: {args.input_path}")
        sys.exit(1)
    if not args.neo4j_password:
        logging.error("Neo4j password is required. Set --neo4j-password or NEO4J_PASSWORD environment variable.")
        sys.exit(1)
    if not args.neo4j_url:
        logging.error("Neo4j URL is required. Set --neo4j-url or NEO4J_URL environment variable.")
        sys.exit(1)

    # --- Define Paths ---

    # Use absolute path for output directory to avoid issues with relative paths later
    abs_output_dir = os.path.abspath(args.output_dir)
    # Define specific file/dir names within the output directory
    cpg_file_path = os.path.join(abs_output_dir, "cpg.bin")
    csv_export_dir = os.path.join(abs_output_dir, "neo4j_csv")
    output_dir_path = Path(csv_export_dir).resolve() # Use resolved absolute path

    # Define the Neo4j import directory (adjust if your setup differs)
    # This path is specific to Neo4j Desktop on macOS for a particular DB instance
    # NEO4J_IMPORT_DIR = Path("/Users/tenhitokiri/Library/Application Support/Neo4j Desktop/Application/relate-data/dbmss/dbms-208c6b9e-421f-40a0-90c1-d54f6b80a942/import")
    NEO4J_IMPORT_DIR = Path(args.neo4j_import_dir).resolve()

    # --- Step 1: Run Joern Parse ---
    cpg_path = run_joern_parse(args.input_path, cpg_file_path, args.jvm_mem)
    if not cpg_path:
        logging.error("Exiting due to Joern parsing failure.")
        sys.exit(1)

    # --- Step 2: Run Joern Export ---
    # First, remove the output directory if it exists, as joern-export requires it not to exist.
    if os.path.exists(csv_export_dir):
        logging.info(f"Removing existing export directory: {csv_export_dir}")
        try:
            shutil.rmtree(csv_export_dir)
        except Exception as e:
            logging.error(f"Failed to remove existing export directory {csv_export_dir}: {e}")
            sys.exit(1)

    csv_dir = run_joern_export(cpg_path, csv_export_dir, jvm_mem=args.jvm_mem)
    if not csv_dir:
        logging.error("Exiting due to Joern export failure.")
        sys.exit(1)

    # --- Step 2.5: Copy Data Files to Neo4j Import Directory ---
    try:
        logging.info(f"Ensuring Neo4j import directory exists: {NEO4J_IMPORT_DIR}")
        NEO4J_IMPORT_DIR.mkdir(parents=True, exist_ok=True)

        logging.info(f"Copying *_data.csv files from {output_dir_path} to {NEO4J_IMPORT_DIR}...")
        copied_count = 0
        for data_file in output_dir_path.glob("*_data.csv"):
            if data_file.is_file():
                try:
                    shutil.copy(data_file, NEO4J_IMPORT_DIR)
                    copied_count += 1
                    # logging.debug(f"Copied {data_file.name}") # Uncomment for verbose logging
                except Exception as copy_err:
                    logging.error(f"Failed to copy {data_file.name} to {NEO4J_IMPORT_DIR}: {copy_err}")
        logging.info(f"Finished copying {copied_count} data files.")

    except Exception as e:
        logging.error(f"Error during data file copying process: {e}")
        sys.exit(1)


    # --- Step 3: Import to Neo4j ---
    logging.info(f"Starting Joern Neo4j import process...")
    logging.info(f"Looking for Cypher/Data files in: {output_dir_path}")
    logging.info(f"Target Neo4j URI: {args.neo4j_uri}")
    logging.info(f"Target Neo4j User: {args.neo4j_user}")
    logging.info(f"Target Neo4j Database: {args.neo4j_database}")

    driver = None
    import_successful = False
    try:
        # --- Discover Files ---
        node_cypher_files, edge_cypher_files = get_cypher_files(output_dir_path)

        if not node_cypher_files and not edge_cypher_files:
            logging.info("No Cypher files found to process. Exiting.")
            sys.exit(0) # Exit successfully if no files found

        # --- Connect to Neo4j ---
        logging.info("Connecting to Neo4j...")
        driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password))
        # Verify connectivity against the target database
        with driver.session(database=args.neo4j_database) as session:
            session.run("RETURN 1")
            logging.info("Neo4j connection successful.")

        # --- Run Import ---
        import_successful = import_online_neo4j(
            driver, args.neo4j_database, node_cypher_files, edge_cypher_files, output_dir_path
        )

    except FileNotFoundError as e:
        logging.error(f"Error: {e}")
    except neo4j_exceptions.AuthError:
        logging.error(f"Neo4j authentication failed for user '{args.neo4j_user}'. Check credentials.")
    except neo4j_exceptions.ServiceUnavailable:
        logging.error(f"Could not connect to Neo4j at {args.neo4j_uri}. Ensure the server is running and accessible.")
    except neo4j_exceptions.ConfigurationError as ce:
        logging.error(f"Neo4j configuration error: {ce}")
        logging.error("This might indicate an issue connecting to the specified database if it doesn't exist or is unavailable.")
    except Exception as e:
        logging.error(f"An unexpected error occurred: {type(e).__name__} - {e}")
        import traceback
        logging.error(traceback.format_exc()) # log full traceback for unexpected errors
    finally:
        if driver:
            logging.info("Closing Neo4j connection.")
            driver.close()

    # --- Final Status ---
    if import_successful:
        logging.info("Script execution finished successfully.")
        sys.exit(0)
    else:
        logging.error("Script execution finished with errors.")
        sys.exit(1)


if __name__ == "__main__":
    main()
