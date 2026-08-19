(function ($) {
  "use strict";

  function clearSelect($field) {
    if (!$field.length) {
      return;
    }
    $field.val(null).trigger("change");
  }

  $(function () {
    var $owner = $("#id_owner");
    var $product = $("#id_product");
    var $barcodeType = $("#id_barcode_type");
    var $package = $("#id_package");

    function syncPackageState() {
      var packageRequired = ["CARTON", "PACKAGE"].indexOf($barcodeType.val()) !== -1;
      if (!packageRequired) {
        clearSelect($package);
      }
      $package.prop("disabled", !packageRequired);
      $package.prop("required", packageRequired);
      $package.attr("aria-required", packageRequired ? "true" : "false");
    }

    $owner.on("change.productBarcode", function () {
      clearSelect($product);
      clearSelect($package);
    });

    $product.on("change.productBarcode", function () {
      clearSelect($package);
    });

    $barcodeType.on("change.productBarcode", syncPackageState);
    syncPackageState();
  });
})(django.jQuery);
