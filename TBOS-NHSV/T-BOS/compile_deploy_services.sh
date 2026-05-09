#!/bin/bash

# === Root directory of this script ===
HOME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# === Source and log directories ===
TUX_DIR="${HOME_DIR}/TUX_SRC"                 # Folder containing all service source directories
LOG_DIR="${HOME_DIR}/compile_log"             # Folder to store all logs
LOG_FILE="${LOG_DIR}/compile_deploy_$(date +%Y%m%d_%H%M%S).log"  # Main log file with timestamp
ERR_LOG_FILE="${LOG_DIR}/service_err_list.log"                   # Fixed name error log file

# === Prepare log directories ===
mkdir -p "$LOG_DIR"
> "$ERR_LOG_FILE"   # Clear previous error log

# === Options ===
DEPLOY_ENABLED=false   # Whether to perform deploy after compile
DEPLOY_ENV="test"  # Default environment for deployment
SV_LIST=()             # List of services to process
IGNORE_LIST=()         # List of services or patterns to ignore
COUNT=0                # Counter for service index()         # List of services or patterns to ignore

# === Parse command-line arguments ===
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --deploy)
            DEPLOY_ENABLED=true
            ;;
        --sv_list)
            shift
            IFS=',' read -ra SV_LIST <<< "$1"
            ;;
        --ignore_list)
            shift
            IFS=',' read -ra IGNORE_LIST <<< "$1"
            ;;
        --env)
            shift
            DEPLOY_ENV="$1"
            ;;
        *)
            SV_LIST+=("$1")
            ;;
    esac
    shift
done

# === Automatically fetch service list if --sv_list is not provided ===
if [[ ${#SV_LIST[@]} -eq 0 ]]; then
    while IFS= read -r -d $'\0' dir; do
        SV_LIST+=("$(basename "$dir")")
    done < <(find "$TUX_DIR" -mindepth 1 -maxdepth 1 -type d -print0)
else
    # Expand wildcard patterns in sv_list
    EXPANDED_LIST=()
    for pattern in "${SV_LIST[@]}"; do
        while IFS= read -r -d $'\0' match; do
            EXPANDED_LIST+=("$(basename "$match")")
        done < <(find "$TUX_DIR" -mindepth 1 -maxdepth 1 -type d -name "$pattern" -print0)
    done
    SV_LIST=("${EXPANDED_LIST[@]}")
fi

# === Apply ignore_list patterns ===
if [[ ${#IGNORE_LIST[@]} -gt 0 ]]; then
    FILTERED_LIST=()
    for sv in "${SV_LIST[@]}"; do
        skip=false
        for pattern in "${IGNORE_LIST[@]}"; do
            if [[ "$sv" == $pattern ]]; then
                skip=true
                break
            fi
        done
        if ! $skip; then
            FILTERED_LIST+=("$sv")
        fi
    done
    SV_LIST=("${FILTERED_LIST[@]}")

# === Sort SV_LIST alphabetically ===
IFS=$'
' SV_LIST=($(sort <<<"${SV_LIST[*]}"))
unset IFS
fi

# === Log header ===
printf "===================================================================\n" | tee "$LOG_FILE"
printf "Start compiling and deploying Tuxedo services\n" | tee -a "$LOG_FILE"
printf "Start time: %s\n" "$(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG_FILE"
printf "Number of services: %d\n" "${#SV_LIST[@]}" | tee -a "$LOG_FILE"
printf "Deploy: %s (env: %s)\n" "$DEPLOY_ENABLED" "$DEPLOY_ENV" | tee -a "$LOG_FILE"
printf "===================================================================\n" | tee -a "$LOG_FILE"

# === Main loop to compile and deploy each service ===
for service in "${SV_LIST[@]}"; do
    COUNT=$((COUNT + 1))
    SERVICE_PATH="$TUX_DIR/$service"
    printf "\n👉 [%-3d/%-3d] Compile %s...\n" "$COUNT" "${#SV_LIST[@]}" "$service" | tee -a "$LOG_FILE"

    if [[ ! -d "$SERVICE_PATH" ]]; then
        printf "\t❌ Directory not found: %s\n" "$SERVICE_PATH" | tee -a "$LOG_FILE"
        echo "$service : NOT FOUND" >> "$ERR_LOG_FILE"
        continue
    fi

    cd "$SERVICE_PATH" || {
        printf "\t❌ Cannot cd into: %s\n" "$SERVICE_PATH" | tee -a "$LOG_FILE"
        echo "$service : CANNOT CD" >> "$ERR_LOG_FILE"
        continue
    }

    printf "\t🔄 make clean; make\n" | tee -a "$LOG_FILE"
    make clean >> "$LOG_FILE" 2>&1

    if make >> "$LOG_FILE" 2>&1; then
        printf "\t✅ Compile success: %s\n" "$service" | tee -a "$LOG_FILE"

        if $DEPLOY_ENABLED; then
            printf "\t🚀 Deploying [%s] with env: %s\n" "$service" "$DEPLOY_ENV" | tee -a "$LOG_FILE"
            yes Y | make deploy_${DEPLOY_ENV} >> "$LOG_FILE" 2>&1

            if [[ $? -eq 0 ]]; then
                printf "\t✅ Deploy success: %s\n" "$service" | tee -a "$LOG_FILE"
            else
                printf "\t❌ Deploy failed: %s\n" "$service" | tee -a "$LOG_FILE"
                echo "$service : DEPLOY ERROR" >> "$ERR_LOG_FILE"
            fi
        else
            printf "\tℹ️  Skipped deploy step\n" | tee -a "$LOG_FILE"
        fi
    else
        printf "\t❌ Compile failed: %s - skip deploy\n" "$service" | tee -a "$LOG_FILE"
        echo "$service : COMPILE ERROR" >> "$ERR_LOG_FILE"
    fi

done

# === Log footer ===
printf "\n===================================================================\n" | tee -a "$LOG_FILE"
printf "Finished at: %s\n" "$(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG_FILE"
printf "Log file: %s\n" "$LOG_FILE" | tee -a "$LOG_FILE"
printf "Error list: %s\n" "$ERR_LOG_FILE" | tee -a "$LOG_FILE"
printf "===================================================================\n" | tee -a "$LOG_FILE"
