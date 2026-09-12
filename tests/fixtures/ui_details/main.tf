variable "first" {}
variable "second" {}

resource "example_service" "demo" {
  name = var.first
  alias = var.second
}
