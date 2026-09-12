variable "seed" {}
locals { shared = var.seed }
resource "example" "consumer" {
  a = upper(local.shared)
  b = local.shared
  depends_on = [example.other]
}
resource "example" "other" {}
