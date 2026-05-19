import { redirect } from "next/navigation";

export default function GovernancePage() {
  redirect("/pharmaNavigator?space=control");
}
