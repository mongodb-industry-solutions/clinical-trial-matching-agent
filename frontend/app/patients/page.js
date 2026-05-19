import { redirect } from "next/navigation";

function firstValue(value) {
  if (Array.isArray(value)) return value[0] || "";
  return value || "";
}

export default async function PatientsPage({ searchParams }) {
  const params = await searchParams;
  const patientRef = firstValue(params?.patientRef) || firstValue(params?.patient) || "";
  const appointmentId = firstValue(params?.appointmentId) || firstValue(params?.appointmentID) || "";
  const patientId = firstValue(params?.patientId) || firstValue(params?.patientID) || "";

  const next = new URLSearchParams();
  next.set("space", "patient");
  if (patientRef) next.set("patientRef", patientRef);
  if (appointmentId) next.set("appointmentId", appointmentId);
  if (patientId) next.set("patientId", patientId);

  redirect(`/pharmaNavigator?${next.toString()}`);
}
