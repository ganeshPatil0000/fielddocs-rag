# FTTP ONT Installation and Fault Guide

> Synthetic sample document for the FieldDocs RAG demo. Not a real operator manual.

## Safety before you start

Never look directly into the end of a fibre connector or an open adapter port. Class 1M laser light is invisible and can damage your eyes. Always fit dust caps to unused ports and use an optical power meter, not your eyes, to check for light.

Dispose of fibre offcuts in the sealed sharps container supplied in the van kit. Fibre shards can penetrate skin and are hard to remove.

## Mounting the ONT

Mount the Optical Network Terminal (ONT) on an internal wall within 1.5 metres of a mains socket. Do not mount the ONT in a loft, garage or bathroom. Leave at least 10 cm of clearance on all sides for ventilation.

The minimum bend radius for the internal fibre drop cable is 30 mm. Tighter bends cause macro-bend loss and intermittent faults that are hard to trace later.

## ONT status lights

- POWER solid green: unit is powered normally.
- PON solid green: the ONT is registered on the optical network.
- PON flashing green: the ONT is trying to register. Wait up to 5 minutes before escalating.
- LOS solid red: loss of signal. No light is reaching the ONT.
- LOS flashing red: received optical power is too low.
- ALARM solid amber: hardware fault. Replace the ONT.

## Fault code LOS (loss of signal)

1. Check that the fibre patch lead is fully seated in the ONT optical port.
2. Clean the connector end face with a one-click cleaner and refit it.
3. Measure received power at the ONT with the optical power meter.
4. If received power is below -27 dBm, test the drop cable back to the customer splitter node.
5. If the drop cable tests good, raise a network fault with the code LOS-NET and the meter reading.

Healthy received power at the ONT is between -8 dBm and -27 dBm.

## Registration fault PON flashing for more than 5 minutes

Confirm the ONT serial number on the work order matches the label on the unit. A mismatch means the network has the wrong ONT provisioned. Update the serial number in the field app and power-cycle the ONT. If PON is still flashing after 10 minutes, call the provisioning desk.
