resource "example_network" "app" {}

resource "example_service" "api" {
  network_id = example_network.app.id
}

output "api_network" {
  value = example_service.api.network_id
}
