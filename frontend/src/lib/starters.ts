/**
 * Example questions on the empty chat screen, grouped by type of job.
 * They match the sample documents; change them to fit the manuals you index.
 */
export const STARTERS: { job: string; questions: string[] }[] = [
  {
    job: "Fibre installs",
    questions: [
      "What received power is healthy at the ONT?",
      "Which fault code do I raise if the drop cable tests good?",
    ],
  },
  {
    job: "Copper faults",
    questions: [
      "Loop resistance is above 1300 ohms, what does that indicate?",
      "How many sync drops in 24 hours should trigger a noise check?",
    ],
  },
  {
    job: "Cabinet power",
    questions: [
      "What does a MAINS FAIL alarm mean?",
      "How often should street cabinet batteries be replaced?",
    ],
  },
];
