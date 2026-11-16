import { PublicJobPage } from "@/features/workflows/pages";
export default async function Page({params}:{params:Promise<{job_id:string}>}){const {job_id}=await params;return <PublicJobPage id={job_id}/>;}
