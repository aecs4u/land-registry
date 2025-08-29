#!/bin/bash

# Land Registry Service - Google Cloud Functions Deployment Script
# This script helps deploy the Land Registry app to Google Cloud Functions

set -e  # Exit on any error

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
PROJECT_ID=""
REGION="europe-west1"
FUNCTION_NAME="land-registry-service"
MEMORY="2GB"
TIMEOUT="540s"
MAX_INSTANCES="10"
ALLOW_UNAUTHENTICATED="true"
ENVIRONMENT="production"

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to show usage
show_usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Deploy Land REgistry Service to Google Cloud Functions

OPTIONS:
    -p, --project PROJECT_ID     Google Cloud Project ID (required)
    -r, --region REGION         Deployment region (default: europe-west1)
    -n, --name FUNCTION_NAME    Function name (default: land-registry-service)
    -m, --memory MEMORY         Memory allocation (default: 2GB)
    -t, --timeout TIMEOUT       Timeout in seconds (default: 540s)
    -i, --instances MAX_INST    Max instances (default: 10)
    -a, --auth                  Require authentication (default: allow unauthenticated)
    -e, --env ENVIRONMENT       Environment (default: production)
    -h, --help                  Show this help message

EXAMPLES:
    $0 -p my-project-id
    $0 -p my-project-id -r europe-west1 -m 4GB -t 600s
    $0 -p my-project-id --auth  # Require authentication

PREREQUISITES:
    - Google Cloud SDK (gcloud) installed and authenticated
    - Google Cloud Functions API enabled
    - Required environment variables set in .env file or Cloud Functions

EOF
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--project)
            PROJECT_ID="$2"
            shift 2
            ;;
        -r|--region)
            REGION="$2"
            shift 2
            ;;
        -n|--name)
            FUNCTION_NAME="$2"
            shift 2
            ;;
        -m|--memory)
            MEMORY="$2"
            shift 2
            ;;
        -t|--timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        -i|--instances)
            MAX_INSTANCES="$2"
            shift 2
            ;;
        -a|--auth)
            ALLOW_UNAUTHENTICATED="false"
            shift
            ;;
        -e|--env)
            ENVIRONMENT="$2"
            shift 2
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

# Validate required parameters
if [[ -z "$PROJECT_ID" ]]; then
    print_error "Project ID is required. Use -p or --project to specify."
    show_usage
    exit 1
fi

# Check prerequisites
print_status "Checking prerequisites..."

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    print_error "Google Cloud SDK (gcloud) is not installed. Please install it first."
    exit 1
fi

# Check if user is authenticated
if ! gcloud auth list --filter="status:ACTIVE" --format="value(account)" | grep -q .; then
    print_error "Not authenticated with Google Cloud. Run: gcloud auth login"
    exit 1
fi

# Check if project exists and user has access
if ! gcloud projects describe "$PROJECT_ID" &> /dev/null; then
    print_error "Cannot access project '$PROJECT_ID'. Check project ID and permissions."
    exit 1
fi

print_success "Prerequisites check passed"

# Set the project
print_status "Setting project to $PROJECT_ID..."
gcloud config set project "$PROJECT_ID"

# Enable required APIs
print_status "Enabling required APIs..."
gcloud services enable cloudfunctions.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable run.googleapis.com

# Check for environment file
if [[ -f ".env" ]]; then
    print_status "Found .env file. Remember to set environment variables in Cloud Functions."
else
    print_warning "No .env file found. Make sure to configure environment variables."
fi

# Prepare deployment command with Cloud Run Gen2 settings
DEPLOY_ARGS=(
    "functions" "deploy" "$FUNCTION_NAME"
    "--source" "."
    "--entry-point" "land_registry"
    "--runtime" "python311"
    "--trigger-http"
    "--region" "$REGION"
    "--memory" "$MEMORY"
    "--timeout" "$TIMEOUT"
    "--max-instances" "$MAX_INSTANCES"
    "--set-env-vars" "ENVIRONMENT=$ENVIRONMENT"
    "--gen2"
    "--cpu" "1"
)

if [[ "$ALLOW_UNAUTHENTICATED" == "true" ]]; then
    DEPLOY_ARGS+=("--allow-unauthenticated")
fi

# Show deployment configuration
print_status "Deployment configuration:"
echo "  Project ID: $PROJECT_ID"
echo "  Region: $REGION"
echo "  Function Name: $FUNCTION_NAME"
echo "  Memory: $MEMORY"
echo "  Timeout: $TIMEOUT"
echo "  Max Instances: $MAX_INSTANCES"
echo "  Allow Unauthenticated: $ALLOW_UNAUTHENTICATED"
echo "  Environment: $ENVIRONMENT"
echo

# Confirm deployment
read -p "Do you want to proceed with deployment? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    print_status "Deployment cancelled."
    exit 0
fi

# Deploy the function
print_status "Deploying function to Google Cloud Functions..."
if gcloud "${DEPLOY_ARGS[@]}"; then
    print_success "Deployment completed successfully!"
    
    # Get the function URL
    FUNCTION_URL=$(gcloud functions describe "$FUNCTION_NAME" --region="$REGION" --format="value(httpsTrigger.url)")
    
    print_success "Function deployed at: $FUNCTION_URL"
    print_status "Health check: curl $FUNCTION_URL/health"
    
    # Test the deployment with longer timeout for Cloud Run Gen2
    print_status "Testing deployment (allowing time for cold start)..."
    sleep 5  # Give Cloud Run some time to initialize
    if curl -s -f --max-time 30 "$FUNCTION_URL/health" > /dev/null; then
        print_success "Health check passed!"
    else
        print_warning "Health check failed. The function might still be initializing."
        print_status "Try manual health check: curl $FUNCTION_URL/health"
    fi
    
else
    print_error "Deployment failed!"
    exit 1
fi

print_status "Deployment completed. Check the Google Cloud Console for more details."