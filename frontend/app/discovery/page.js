import { redirect } from "next/navigation";

export default function DiscoveryPage() {
  redirect("/pharmaNavigator?space=ingestion");
}
