#!/bin/bash

# Land Registry Service - Google Cloud Run Direct Deployment Script
# This script deploys the Land Registry app directly to Cloud Run using Docker

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
SERVICE_NAME="land-registry-service"
MEMORY="2Gi"
CPU="1"
MAX_INSTANCES="10"
MIN_INSTANCES="0"
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

Deploy Land Registry Service to Google Cloud Run

OPTIONS:
    -p, --project PROJECT_ID     Google Cloud Project ID (required)
    -r, --region REGION         Deployment region (default: europe-west1)
    -s, --service SERVICE_NAME  Service name (default: land-registry-service)
    -m, --memory MEMORY         Memory allocation (default: 2Gi)
    -c, --cpu CPU               CPU allocation (default: 1)
    -i, --max-instances MAX     Max instances (default: 10)
    -n, --min-instances MIN     Min instances (default: 0)
    -a, --auth                  Require authentication (default: allow unauthenticated)
    -e, --env ENVIRONMENT       Environment (default: production)
    -h, --help                  Show this help message

EXAMPLES:
    $0 -p my-project-id
    $0 -p my-project-id -r europe-west1 -m 4Gi -c 2
    $0 -p my-project-id --auth  # Require authentication

PREREQUISITES:
    - Google Cloud SDK (gcloud) installed and authenticated
    - Docker installed and running
    - Cloud Run API enabled
    - Cloud Build API enabled (for building containers)

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
        -s|--service)
            SERVICE_NAME="$2"
            shift 2
            ;;
        -m|--memory)
            MEMORY="$2"
            shift 2
            ;;
        -c|--cpu)
            CPU="$2"
            shift 2
            ;;
        -i|--max-instances)
            MAX_INSTANCES="$2"
            shift 2
            ;;
        -n|--min-instances)
            MIN_INSTANCES="$2"
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

# Check if docker is installed
if ! command -v docker &> /dev/null; then
    print_error "Docker is not installed. Please install it first."
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
gcloud services enable run.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable containerregistry.googleapis.com

# Show deployment configuration
print_status "Deployment configuration:"
echo "  Project ID: $PROJECT_ID"
echo "  Region: $REGION"
echo "  Service Name: $SERVICE_NAME"
echo "  Memory: $MEMORY"
echo "  CPU: $CPU"
echo "  Max Instances: $MAX_INSTANCES"
echo "  Min Instances: $MIN_INSTANCES"
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

# Build and deploy to Cloud Run
print_status "Building and deploying to Cloud Run..."

# Prepare deployment args
DEPLOY_ARGS=(
    "run" "deploy" "$SERVICE_NAME"
    "--source" "."
    "--region" "$REGION"
    "--memory" "$MEMORY"
    "--cpu" "$CPU"
    "--max-instances" "$MAX_INSTANCES"
    "--min-instances" "$MIN_INSTANCES"
    "--set-env-vars" "ENVIRONMENT=$ENVIRONMENT"
    "--port" "8080"
    "--timeout" "600"
)

if [[ "$ALLOW_UNAUTHENTICATED" == "true" ]]; then
    DEPLOY_ARGS+=("--allow-unauthenticated")
fi

# Deploy the service
if gcloud "${DEPLOY_ARGS[@]}"; then
    print_success "Deployment completed successfully!"
    
    # Get the service URL
    SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" --region="$REGION" --format="value(status.url)")
    
    print_success "Service deployed at: $SERVICE_URL"
    print_status "Health check: curl $SERVICE_URL/health"
    
    # Test the deployment
    print_status "Testing deployment (allowing time for cold start)..."
    sleep 5  # Give Cloud Run some time to initialize
    if curl -s -f --max-time 30 "$SERVICE_URL/health" > /dev/null; then
        print_success "Health check passed!"
    else
        print_warning "Health check failed. The service might still be initializing."
        print_status "Try manual health check: curl $SERVICE_URL/health"
    fi
    
else
    print_error "Deployment failed!"
    exit 1
fi

print_status "Deployment completed. Check the Google Cloud Console for more details."