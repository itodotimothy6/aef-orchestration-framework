terraform {
  backend "gcs" {
    bucket = "aef-tim-pso-training-project-tfe"
    prefix = "aef-orchestration-framework/environments/dev"
  }
}