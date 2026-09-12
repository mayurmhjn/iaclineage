variable "name" {
  type = string
}

module "app" {
  source   = "./modules/app"
  app_name = var.name
}

output "app_id" {
  value = module.app.id
}

output "missing" {
  value = module.app.does_not_exist
}
