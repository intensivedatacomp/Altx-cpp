@mainpage altx

@brief A C++ port of the Python [`altx`](https://github.com/halmosb/altx) package — Adaptive
Law-Based Transformation for time series.

[TOC]

@section mainpage_what What altx does

Adaptive Law-Based Transformation turns a labelled collection of time series into a fixed-length
feature vector per instance, in two phases.

**Training** slides a window over every channel of every instance and embeds each window into a
symmetric @f$ l \times l @f$ matrix. The eigenvector belonging to the smallest *absolute* eigenvalue of that
matrix is kept as a **law** — the linear relation the window most nearly satisfies. A training run
is millions of independent tiny eigenproblems, and the laws it produces are the model.

**Transformation** projects an unseen instance onto every law, one thin matrix product per channel,
and squares the result. A window that obeys a law projects to nearly zero, so a small value is
evidence for the class that law was learned from. The law axis is split by class and reduced twice:
a quantile along the law axis, then a statistic — the mean, the variance, the excess kurtosis —
along the window axis.

@section mainpage_status The state of the port

The build system, the container images and the quality gates are in place; the algorithm is not.
`src/` currently holds nothing but the template for the generated version header, so the API
reference is empty until the first kernel lands, and the pages below describe how to work on the
repository rather than how to use a program.

`DevelopmentPlan.md`, in the repository root, is the authoritative record of what arrives when and
why each decision was made — usually with the rejected alternative alongside it.

@section mainpage_pages Pages

| Page | What it covers |
| --- | --- |
| @ref development_environment | The Docker images, the ways of running them, and the editor tooling they carry |
| @ref code_quality | Every automated check in the repository: what runs, where, how to configure it and how to silence one when it is wrong |

@section mainpage_reference The reference implementation

The Python package this ports is the correctness oracle rather than merely the inspiration: both
implementations read and write the same HDF5 schema, so a model trained by one can be handed to the
other and the features compared. Where the two deliberately disagree — and there are a few places,
each argued in the plan — the difference is documented, so that a round-trip test can assert it
instead of reporting it as a failure.
