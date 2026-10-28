# Copper Line Fault Diagnosis (DSL)

> Synthetic sample document for the FieldDocs RAG demo. Not a real operator manual.

## Line test procedure

Run a full line test from the handheld tester at the master socket with all customer equipment disconnected. Record the results in the field app before making any changes, so the before-and-after comparison is captured.

## Insulation resistance

Insulation resistance between the A and B legs, and from each leg to earth, should be at least 1 megohm. A reading below 1 megohm indicates a wet joint or damaged cable and the line must not be signed off.

## Loop resistance

Loop resistance above 1300 ohms on a standard line points to a high-resistance joint. Check joints in the distribution point first, then at the cabinet.

## Noise and DSL sync problems

If the DSL connection drops sync more than 3 times in 24 hours, check the line for noise with the tester in noise mode. Common causes are unfiltered extension wiring, faulty microfilters and electrical interference from nearby equipment.

Fit a new faceplate filter to the master socket and remove the extension wiring from the test path. Re-test after 15 minutes of stable sync.

## Earth contact fault

An earth contact fault appears as low resistance from one leg to earth. Disconnect the line at the distribution point and test in both directions to find which section holds the fault.
