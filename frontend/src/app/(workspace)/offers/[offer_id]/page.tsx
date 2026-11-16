import { OfferDetailPage } from "@/features/workflows/pages";
export default async function Page({params}:{params:Promise<{offer_id:string}>}){const {offer_id}=await params;return <OfferDetailPage id={offer_id}/>;}
