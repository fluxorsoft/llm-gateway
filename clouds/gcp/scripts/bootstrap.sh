#!/usr/bin/env bash
# Once per gateway project, by someone who owns it and the team projects: APIs, the Terraform state bucket, the image
# repository, and a keyless identity for GitHub Actions that only this repository's "gateway" environment can use.
#   PROJECT=... TEAM_PROJECTS="a b" GITHUB_REPO=owner/repo REGION=europe-west2 [CMEK=1] clouds/gcp/scripts/bootstrap.sh
set -euo pipefail
: "${PROJECT:?}" "${TEAM_PROJECTS:?}" "${GITHUB_REPO:?}" "${REGION:=europe-west2}" "${NAME:=llmgw}" "${REPO_LOCATION:=europe}"
: "${GITHUB_ENVIRONMENT=gateway}" # set it empty for none (private repositories on GitHub Free have no environments)
g() { gcloud --project="$PROJECT" --quiet "$@"; }
bind() { gcloud projects add-iam-policy-binding "$1" --quiet --member="serviceAccount:$deployer" --role="roles/$2" --condition=None --format=none; }

g services enable run.googleapis.com compute.googleapis.com sqladmin.googleapis.com redis.googleapis.com \
  secretmanager.googleapis.com modelarmor.googleapis.com artifactregistry.googleapis.com dns.googleapis.com \
  certificatemanager.googleapis.com logging.googleapis.com monitoring.googleapis.com bigquery.googleapis.com \
  iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com cloudscheduler.googleapis.com \
  networkconnectivity.googleapis.com cloudresourcemanager.googleapis.com serviceusage.googleapis.com
g storage buckets describe "gs://$PROJECT-$NAME-tfstate" > /dev/null 2>&1 || {
  g storage buckets create "gs://$PROJECT-$NAME-tfstate" --location="$REGION" --uniform-bucket-level-access --public-access-prevention
  g storage buckets update "gs://$PROJECT-$NAME-tfstate" --versioning
}
g artifacts repositories describe "$NAME" --location="$REPO_LOCATION" > /dev/null 2>&1 ||
  g artifacts repositories create "$NAME" --location="$REPO_LOCATION" --repository-format=docker

# with your own encryption keys (cmek_keys), the service agents that use them must exist first
if [ "${CMEK:-0}" = 1 ]; then
  for s in sqladmin.googleapis.com secretmanager.googleapis.com; do gcloud beta services identity create --service="$s" --project="$PROJECT" > /dev/null; done
fi

deployer="$NAME-github@$PROJECT.iam.gserviceaccount.com"
g iam service-accounts describe "$deployer" > /dev/null 2>&1 || { g iam service-accounts create "$NAME-github" --display-name="GitHub Actions: deploys the gateway"; sleep 15; }
number="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
# with an environment: only jobs in it (its reviewers always apply); without one: only the repository's main branch,
# so anyone who can write to main can deploy
if [ -n "$GITHUB_ENVIRONMENT" ]; then
  condition="assertion.repository == '$GITHUB_REPO' && assertion.environment == '$GITHUB_ENVIRONMENT'"
else
  condition="assertion.repository == '$GITHUB_REPO' && assertion.ref == 'refs/heads/main'"
fi
g iam workload-identity-pools describe "$NAME-github" --location=global > /dev/null 2>&1 ||
  g iam workload-identity-pools create "$NAME-github" --location=global
g iam workload-identity-pools providers describe github --workload-identity-pool="$NAME-github" --location=global > /dev/null 2>&1 ||
  g iam workload-identity-pools providers create-oidc github --workload-identity-pool="$NAME-github" --location=global \
    --issuer-uri=https://token.actions.githubusercontent.com \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition="$condition"
# the provider accepts only this repository's jobs that run in the environment, so its protection rules always apply
g iam service-accounts add-iam-policy-binding "$deployer" --role=roles/iam.workloadIdentityUser --format=none \
  --member="principalSet://iam.googleapis.com/projects/$number/locations/global/workloadIdentityPools/$NAME-github/attribute.repository/$GITHUB_REPO"

for r in run.admin compute.admin cloudsql.admin redis.admin secretmanager.admin modelarmor.admin artifactregistry.admin \
  dns.admin certificatemanager.owner logging.admin monitoring.editor bigquery.admin iam.serviceAccountAdmin \
  iam.serviceAccountUser resourcemanager.projectIamAdmin storage.admin serviceusage.serviceUsageAdmin cloudscheduler.admin \
  servicedirectory.editor compute.networkAdmin; do
  bind "$PROJECT" "$r"
done
for p in $TEAM_PROJECTS; do
  for r in run.admin compute.networkAdmin secretmanager.admin dns.admin iam.serviceAccountAdmin iam.serviceAccountUser \
    iam.roleAdmin resourcemanager.projectIamAdmin serviceusage.serviceUsageAdmin browser; do
    bind "$p" "$r"
  done
done
echo
if [ -n "$GITHUB_ENVIRONMENT" ]; then echo "Set these as variables of the repository's '$GITHUB_ENVIRONMENT' environment:"; else echo "Set these as variables of the repository:"; fi
echo "  GCP_PROJECT=$PROJECT"
echo "  GCP_IDENTITY=$deployer"
echo "  GCP_PROVIDER=projects/$number/locations/global/workloadIdentityPools/$NAME-github/providers/github"
