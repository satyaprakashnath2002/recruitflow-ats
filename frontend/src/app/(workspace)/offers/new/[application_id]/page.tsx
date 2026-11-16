import { NewOfferPage } from "@/features/workflows/pages";
export default async function Page({params}:{params:Promise<{application_id:string}>}){const {application_id}=await params;return <NewOfferPage applicationId={application_id}/>;}
