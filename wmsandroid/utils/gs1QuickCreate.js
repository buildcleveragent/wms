function cleanTrackingValue(value) {
  return String(value ?? '').trim()
}

export function inferGs1TrackingControls(form = {}) {
  const lotNo = cleanTrackingValue(form.lot_no)
  const expiryControl = [
    form.shelf_life_days,
    form.inbound_valid_days,
    form.expiry_warning_days,
    form.mfg_date,
    form.exp_date,
  ].some((value) => cleanTrackingValue(value) !== '')

  return {
    batch_control: lotNo !== '',
    lot_no: lotNo,
    expiry_control: expiryControl,
  }
}
