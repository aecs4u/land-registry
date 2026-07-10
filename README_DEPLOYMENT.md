# Land Registry Deployment Guide

This guide covers deploying the Land Registry application to Google Cloud Run using the updated deployment script.

## 🚀 Quick Start

### 1. Setup Configuration

Copy the example configuration:
```bash
cp deploy-config.yaml.example deploy-config.yaml
```

Edit `deploy-config.yaml` with your project settings:
```yaml
gcp:
  project_id: "your-actual-project-id"
  region: "europe-west1"
  service_name: "your-service-name"

cloudrun:
  memory: "2Gi"
  cpu: "2"
  min_instances: "0"
  max_instances: "10"
  # ... etc
```

### 2. Deploy

#### Option A: Non-interactive deployment
```bash
./deploy-cloudrun.sh
```

#### Option B: Interactive deployment
```bash
./deploy-cloudrun.sh --interactive
```

#### Option C: With custom config file
```bash
./deploy-cloudrun.sh --config my-config.yaml
```

## 📋 Required Configuration

The script now requires **ALL** configuration values. No defaults are provided to make it truly flexible:

### GCP Settings (Required)
- `project_id` - Your Google Cloud Project ID
- `region` - Deployment region (e.g., europe-west1, us-central1)
- `service_name` - Cloud Run service name

### Cloud Run Settings (Required)
- `memory` - Memory allocation (e.g., "1Gi", "2Gi", "4Gi")
- `cpu` - CPU allocation (1, 2, 4, etc.)
- `min_instances` - Minimum instances (usually 0)
- `max_instances` - Maximum instances (1-1000)
- `concurrency` - Max concurrent requests per instance (1-10000)
- `execution_environment` - "gen1" or "gen2"
- `allow_unauthenticated` - "true" or "false"
- `startup_probe_timeout` - Timeout in seconds (60-3600)

### Docker Settings (Required)
- `base_image` - Base Docker image (e.g., "python:3.11-slim")
- `working_dir` - Working directory in container (e.g., "/app")
- `exposed_port` - Port to expose (usually "8080")

## 🔧 Script Features

### Command Line Options
```bash
./deploy-cloudrun.sh [options]

Options:
  --config FILE       Use custom config file (default: deploy-config.yaml)
  --interactive       Enable interactive configuration mode
  --dry-run          Show what would be deployed without actually deploying
  --skip-build       Skip Docker build step
  --skip-deploy      Skip Cloud Run deployment step
  --list-backups     List available service backups
  --rollback FILE    Rollback to a specific backup
  --help             Show help message
```

### Configuration Sources (Priority Order)
1. Command line arguments
2. Configuration file (YAML)
3. Environment variables
4. Interactive prompts (if --interactive)

### Environment Variables
You can also set configuration via environment variables:
```bash
export GCP_PROJECT_ID="your-project-id"
export GCP_REGION="europe-west1"
export CLOUDRUN_SERVICE_NAME="land-registry"
# ... etc
```

## 🧪 Testing Before Deployment

Always run tests before deploying:

```bash
# Run full test suite with coverage
make test-cov

# Run only integration tests
make test-integration

# Generate HTML coverage report
make test-html
```

## 🔐 Security Considerations

### Secrets Management
- **Never** put sensitive data in config files
- Use Google Secret Manager for:
  - Database credentials
  - API keys
  - S3 credentials
  - Other sensitive configuration

### Example Secret Manager Setup
```bash
# Store S3 credentials in Secret Manager
gcloud secrets create s3-access-key --data-file=- <<< "your-access-key"
gcloud secrets create s3-secret-key --data-file=- <<< "your-secret-key"

# Grant Cloud Run access to secrets
gcloud projects add-iam-policy-binding PROJECT_ID \
    --member="serviceAccount:SERVICE_ACCOUNT@PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
```

## 📊 Monitoring and Logging

### Deployment Monitoring
The script provides comprehensive logging:
- Deployment progress tracking
- Configuration validation
- Error reporting with context
- Backup creation before updates

### Application Monitoring
Monitor your deployed application:
```bash
# View logs
gcloud run services logs read land-registry-service --region=europe-west1

# Check service status
gcloud run services describe land-registry-service --region=europe-west1
```

## 🔄 Rollback Procedures

### List Available Backups
```bash
./deploy-cloudrun.sh --list-backups
```

### Rollback to Previous Version
```bash
./deploy-cloudrun.sh --rollback backup-filename.yaml
```

## 🚨 Troubleshooting

### Common Issues

1. **Missing Configuration**
   ```
   ERROR: PROJECT_ID is required
   ```
   **Solution**: Ensure all required values are in your config file

2. **Authentication Issues**
   ```
   ERROR: Unable to authenticate with GCP
   ```
   **Solution**: Run `gcloud auth login` and `gcloud config set project PROJECT_ID`

3. **Insufficient Permissions**
   ```
   ERROR: Permission denied
   ```
   **Solution**: Ensure your account has Cloud Run Admin and Storage Admin roles

4. **Build Failures**
   ```
   ERROR: Docker build failed
   ```
   **Solution**: Check Dockerfile and ensure all dependencies are available

### Debug Mode
Enable verbose logging:
```bash
./deploy-cloudrun.sh --interactive --verbose
```

## 📈 Scaling Configuration

### For Development
```yaml
cloudrun:
  memory: "1Gi"
  cpu: "1"
  min_instances: "0"
  max_instances: "2"
  concurrency: "100"
```

### For Production
```yaml
cloudrun:
  memory: "4Gi"
  cpu: "4"
  min_instances: "1"
  max_instances: "100"
  concurrency: "1000"
```

### For High Traffic
```yaml
cloudrun:
  memory: "8Gi"
  cpu: "4"
  min_instances: "5"
  max_instances: "1000"
  concurrency: "1000"
```

## 🎯 Best Practices

1. **Test Locally First**
   ```bash
   uvicorn land_registry.app:app --host 0.0.0.0 --port 8080
   ```

2. **Use Staging Environment**
   - Deploy to staging first
   - Run integration tests against staging
   - Then deploy to production

3. **Monitor Resource Usage**
   - Start with conservative settings
   - Monitor and adjust based on actual usage
   - Use Cloud Monitoring dashboards

4. **Backup Before Updates**
   - The script automatically creates backups
   - Test rollback procedures
   - Keep multiple backup versions

5. **Environment-Specific Configs**
   ```bash
   # Development
   ./deploy-cloudrun.sh --config deploy-dev.yaml

   # Staging
   ./deploy-cloudrun.sh --config deploy-staging.yaml

   # Production
   ./deploy-cloudrun.sh --config deploy-prod.yaml
   ```