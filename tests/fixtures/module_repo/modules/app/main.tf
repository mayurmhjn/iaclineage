variable "app_name" {
  type = string
}

resource "aws_thing" "this" {
  name = var.app_name
}

output "id" {
  value = aws_thing.this.id
}
