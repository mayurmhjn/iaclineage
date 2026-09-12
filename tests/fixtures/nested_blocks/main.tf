variable "size" {}
variable "username" {}
variable "key_path" {}
variable "hostname" {}
resource "aws_instance" "this" {
  root_block_device {
    volume_size = "${var.size}"
  }
  connection {
    host = self.public_ip
    user = "${var.username}"
    private_key = "${file("${var.key_path}")}"
  }
  provisioner "remote-exec" {
    inline = ["COMMAND_CANARY ${var.hostname} ${self.id}", "SECOND_CANARY ${var.username}"]
  }
  provisioner "remote-exec" {
    script = "${path.module}/SCRIPT_CANARY.sh"
    connection { user = var.username }
  }
  provisioner "file" {
    source = "${path.module}/FILE_CANARY.sh"
    destination = "/home/${var.username}/DESTINATION_CANARY"
  }
  provisioner "local-exec" { command = "LOCAL_CANARY ${var.hostname}" }
  provisioner "LABEL_CANARY" {}
  arbitrary "OTHER_LABEL_CANARY" {}
}
