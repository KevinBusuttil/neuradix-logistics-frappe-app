app_name = "neuradix_cassar_ops"
app_title = "Neuradix Cassar Operations"
app_publisher = "Busuttil Technologies Limited"
app_description = "CassarCamilleri warehouse picking and transport operations for Neuradix"
app_email = "kevin.busuttil@busuttil-technologies.com"
app_license = "mit"

required_apps = ["erpnext", "insight", "neuradix", "neuradix_cassarcamilleri"]

after_install = "neuradix_cassar_ops.install.after_install"
after_migrate = "neuradix_cassar_ops.install.after_migrate"
